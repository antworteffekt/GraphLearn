import utils.myeden as graphlearn_utils
import networkx as nx
import itertools
import random
import logging
import postprocessing
import estimator
from graphtools import extract_core_and_interface, core_substitution, graph_clean
from feasibility import FeasibilityChecker
from localsubstitutablegraphgrammar import LocalSubstitutableGraphGrammar
import joblib
import dill
logger = logging.getLogger('log')
logger.setLevel(logging.DEBUG)
formatter = logging.Formatter('%(message)s')
cons = logging.StreamHandler()
cons.setLevel(logging.INFO)
cons.setFormatter(formatter)
logger.addHandler(cons)
file = logging.FileHandler('run.log', mode='w')
file.setLevel(logging.DEBUG)
file.setFormatter(formatter)
logger.addHandler(file)


class GraphLearnSampler:

    def __init__(self, radius_list=[3, 5], thickness_list=[2, 4], estimator=None, grammar=None, nbit=20,
                    vectorizer= graphlearn_utils.GraphLearnVectorizer(complexity=3)):



        self.feasibility_checker = FeasibilityChecker()
        self.postprocessor = postprocessing.postprocessor()

        # see utils.myeden.GraphLeanVectorizer,
        # edens vectorizer assumes that graphs are not expanded.
        # this is fixed with just a few lines of code.
        self.vectorizer = vectorizer


        # lists of int
        self.radius_list = radius_list
        self.thickness_list = thickness_list
        # scikit  classifier
        self.estimator = estimator
        # grammar object
        self.local_substitutable_graph_grammar = grammar
        # cips hashes will be masked with this
        self.hash_bitmask = pow(2, nbit) - 1
        self.nbit = nbit
        # boolean values to set restrictions on replacement
        self.same_radius = None
        self.same_core_size = None
        # a similaritythreshold at which to stop sampling.  a value <= 0 will render this useless
        self.similarity = None
        # we will save current graph at every intervalth step of sampling and attach to graphinfos[graphs]
        self.sampling_interval = None
        # how many sampling steps are done
        self.n_steps = None
        # number of jobs created by multiprocessing  -1 to let python guess how many cores you have
        self.n_jobs = None
        # currently stores information on why the sampling was stopped before n_steps ; will be attached to the graphinfo
        # returned by _sample()
        self._sample_notes = None
        # factor for simulated annealing, 0 means off
        # 1 is pretty strong. 0.6 seems ok
        self.annealing_factor = None

        #current step in sampling proces of a single graph
        self.step= None

    def save(self, file_name):
        self.local_substitutable_graph_grammar.revert_multicore_transform()

        dill.dump(self.__dict__, open(file_name, "w"),protocol=dill.HIGHEST_PROTOCOL)
        #joblib.dump(self.__dict__, file_name, compress=1)

    def load(self, file_name):
        #self.__dict__ = joblib.load(file_name)
        self.__dict__ = dill.load(open(file_name))



    def fit(self, G_pos,
            core_interface_pair_remove_threshold=3,
            interface_remove_threshold=2,
            n_jobs=-1, nu=.5):
        """
          use input to fit the grammar and fit the estimator
        """
        G_iterator, G_iterator_ = itertools.tee(G_pos)

        # get grammar
        self.local_substitutable_graph_grammar = LocalSubstitutableGraphGrammar(self.radius_list, self.thickness_list,
                                                                                core_interface_pair_remove_threshold,
                                                                                interface_remove_threshold,
                                                                                nbit=self.nbit)
        self.local_substitutable_graph_grammar.fit(G_iterator,n_jobs)

        # get estimator
        self.estimator = estimator.fit(G_iterator_,vectorizer=self.vectorizer,nu=nu,n_jobs=n_jobs)





    ############################### SAMPLE ###########################



    def sample(self, graph_iter, same_radius=False, same_core_size=True, similarity=-1, sampling_interval=9999,
               batch_size=10,
               n_jobs=0,
               n_steps=50,
               annealing_factor=0):
        """
            input: graph iterator
            output: yield (sampled_graph,{dictionary of info about sampling process}
        """
        self.same_radius = same_radius
        self.similarity = similarity
        self.sampling_interval = sampling_interval
        self.n_steps = n_steps
        self.n_jobs = n_jobs
        self.same_core_size = same_core_size
        self.annealing_factor = annealing_factor

        # adapt grammar to task:
        self.local_substitutable_graph_grammar.preprocessing(n_jobs,same_radius,same_core_size)

        # do the improvement
        if n_jobs in [0, 1]:
            for graph in graph_iter:
                yield self._sample(graph)
        else:
            _sample_multi= lambda s,graphs: [s._sample(g) for g in graphs]
            for pair in graphlearn_utils.multiprocess(graph_iter,_sample_multi,self,n_jobs=n_jobs,batch_size=batch_size):
                yield pair



    def _sample(self, graph):
        '''
            we sample a single graph.

            input: a graph
            output: (sampled_graph,{info dictionary})
        '''

        if graph==None:
            return None
        # prepare variables and graph
        graph = self._sample_init(graph)
        scores = [graph.score]
        sample_path = [graph]
        accept_counter = 0

        try:
            for self.step in xrange( self.n_steps):
                # check similarity - stop condition..
                self.similarity_checker(graph)



                # get a proposal for a new graph
                # keep it if we like it
                candidate_graph = self.propose(graph)
                if self.accept(graph, candidate_graph):
                    accept_counter += 1
                    graph = candidate_graph


                # save score
                # take snapshot
                scores.append(graph.score)
                if self.step % self.sampling_interval == 0:
                    sample_path.append(graph)


        except Exception as exc:
            logger.info(exc)
            self._sample_notes += "\n"+str(exc)
            self._sample_notes += '\nstoped at step %d' % self.step



        scores += [scores[-1]] * (self.n_steps + 1 - len(scores))
        # we put the result in the sample_path
        # and we return a nice graph as well as a dictionary of additional information
        sample_path.append(graph)
        sampled_graph = self.vectorizer._revert_edge_to_vertex_transform(graph)
        sampled_graph_info =  {'graphs': sample_path, 'score_history': scores, "accept_count": accept_counter, 'notes': self._sample_notes}
        return (sampled_graph, sampled_graph_info)





    def _sample_init(self, graph):
        '''
        we prepare the sampling process

        - first we expand its edges to nodes, so eden will be able wo work its magic on it
        - then we calculate a score for the graph, to see how much we like it
        - we setup the similarity checker stop condition
        - possibly we are in a multiprocessing process, and this class instance hasnt been used before,
          in this case we need to rebuild the postprocessing function .
        '''
        graph = self.vectorizer._edge_to_vertex_transform(graph)
        self.score(graph)
        self._sample_notes = ''
        return graph

    def similarity_checker(self, graph):
        '''
        always check if similarity is relevant.. if so then:

        if current step is zero:

            remember the vectorized object
        else:
            similarity between start graph and current graph is expected to decrease.
            if similarity meassure smaller than the limit, we stop
            because we dont want to drift further
        '''
        if self.similarity > 0:
            if self.step == 0:
                self.vectorizer._reference_vec = \
                    self.vectorizer._convert_dict_to_sparse_matrix(
                        self.vectorizer._transform(0, nx.Graph(graph)))
            else:
                similarity = self.vectorizer._similarity(graph, [1])

                if  similarity < self.similarity:
                    raise Exception('similarity stop condition reached')




    def score(self, graph):
        """
        :param graph: a graph
        :return: score of graph
        we also set graph.score_nonlog and graph.score
        """
        if not 'score' in graph.__dict__:
            transformed_graph = self.vectorizer.transform2(graph)
            # slow so dont do it..
            #graph.score_nonlog = self.estimator.base_estimator.decision_function(transformed_graph)[0]
            graph.score = self.estimator.predict_proba(transformed_graph)[0][1]
            # print graph.score
        return graph.score

    def accept(self, graph_old, graph_new):
        '''
            return true if graph_new scores higher
        '''

        score_graph_old = self.score(graph_old)
        score_graph_new = self.score(graph_new)
        score_ratio =  score_graph_new / score_graph_old
        if score_ratio > 1.0:
           return True
        score_ratio -= (float(self.step)/self.n_steps) * self.annealing_factor
        return score_ratio > random.random()


    def propose(self, graph):
        """
        starting from 'graph' we construct a novel candidate instance
        return None and a debug log if we fail to do so.

        """
        # finding a legit candidate..
        selected_cip = self.select_cip_for_substitution(graph)

        # see which substitution to make
        candidate_cips = self.select_randomized_cips_from_grammar(selected_cip)
        for cipcount,candidate_cip in enumerate(candidate_cips):

            # substitute and return
            graph_new = core_substitution(graph, selected_cip.graph, candidate_cip.graph)
            graph_clean(graph_new)

            if self.feasibility_checker.check(graph_new):
                return self.postprocessor.postprocess(graph_new)
            # ill leave this here.. use it in case things go wrong oo
            #    draw.drawgraphs([graph, selected_cip.graph, candidate_cip.graph], contract=False)

        raise Exception ("propose failed;received %d cips, all of which failed either at substitution or feasibility  " % cipcount +1)



    def select_randomized_cips_from_grammar(self, cip):
        """
        :param cip: the cip we selected from the graph
        :yields: cips found in the grammar that can replace the input cip

        log to debug on fail
        """
        if not cip:
            raise Exception('select randomized cips from grammar got bad cip')

        hashes=self.filter_chips_get_core_hashes(cip)
        random.shuffle(hashes)
        for core_hash in hashes:
            yield self.local_substitutable_graph_grammar.grammar[cip.interface_hash][core_hash]

        raise Exception('select_randomized_cips_from_grammar didn\'t find any acceptable cip; entries_found %d' %
                        len(hashes))

    def filter_chips_get_core_hashes(self,cip):
        '''
        :param cip: the chip to be replaced
        :return: list of core_hashes of acceptable replacement cips
        '''
        if self.same_radius:
            return self.local_substitutable_graph_grammar.radiuslookup[cip.interface_hash][cip.radius]
        elif self.same_core_size:
            return self.local_substitutable_graph_grammar.core_size[cip.interface_hash][cip.core_nodes_count]
        else:
            return self.local_substitutable_graph_grammar.grammar[cip.interface_hash].keys()



    def select_cip_for_substitution(self, graph):
        """
            selects a chip randomly from the graph.
            root is a node_node and not an edge_node
            radius and thickness are chosen to fit the grammars radius and thickness
        """
        tries = 20
        failcount = 0
        for x in xrange(tries):
            node = random.choice(graph.nodes())
            if 'edge' in graph.node[node]:
                node = random.choice(graph.neighbors(node))
            # random radius and thickness
            radius = random.choice(self.local_substitutable_graph_grammar.radius_list)
            thickness = random.choice(self.local_substitutable_graph_grammar.thickness_list)

            # exteract_core_and_interface will return a list of results, we expect just one so we unpack with [0]
            # in addition the selection might fail because it is not possible to extract at the desired radius/thicknes
            #
            cip = extract_core_and_interface(node, graph, [radius], [thickness], vectorizer=self.vectorizer,
                                             hash_bitmask=self.hash_bitmask)

            if not cip:
                failcount+=1
                continue
            cip=cip[0]
            if self.accept_cip_to_substitute(cip):
                return cip
            else:
                failcount+=1

        raise Exception('select_cip_for_substitution failed because no suiting interface was found, extract failed %d times ' % (failcount))


    def accept_cip_to_substitute(self,cip):
        # if we have a hit in the grammar
        if cip.interface_hash in self.local_substitutable_graph_grammar.grammar:
            #  if we have the same_radius rule implemented:
            if self.same_radius:
                # we jump if that hit has not the right radius
                if not self.local_substitutable_graph_grammar.radiuslookup[cip.interface_hash][cip.radius]:
                    return False
            if self.same_core_size:
                if cip.core_nodes_count not in self.local_substitutable_graph_grammar.core_size[cip.interface_hash]:
                    return False
            return True
        return False







