import networkx as nx
from localsubstitutablegraphgrammar import LocalSubstitutableGraphGrammar
from eden.graph import Vectorizer
import logging
import traceback
import graphtools
from coreinterfacepair import CoreInterfacePair
logger = logging.getLogger(__name__)


class LSGVectorLabels(LocalSubstitutableGraphGrammar):

    def get_cip_extractor(self):
        return self.extract_cores_and_interfaces

    def extract_cores_and_interfaces(self, parameters):
        # happens if batcher fills things up with null
        if parameters[0] is None:
            return None
        try:
            # unpack arguments, expand the graph
            graph, radius_list, thickness_list, vectorizer, hash_bitmask, node_entity_check = parameters
            graph = vectorizer._edge_to_vertex_transform(graph)
            cips = []
            for root_node in graph.nodes_iter():
                if 'edge' in graph.node[root_node]:
                    continue
                cip_list = self.extract_core_and_interface(root_node=root_node,
                                                           graph=graph,
                                                           radius_list=radius_list,
                                                           thickness_list=thickness_list,
                                                           vectorizer=vectorizer,
                                                           hash_bitmask=hash_bitmask,
                                                           filter=node_entity_check)

                if cip_list:
                    cips.append(cip_list)
            return cips

        except Exception:
            logger.debug(traceback.format_exc(10))
            # as far as i remember this should almost never happen,
            # if it does you may have a bigger problem.
            # so i put this in info
            # logger.info( "extract_cores_and_interfaces_died" )
            # logger.info( parameters )

    def extract_core_and_interface(self,
                                   root_node=None,
                                   graph=None,
                                   radius_list=None,
                                   thickness_list=None,
                                   vectorizer=Vectorizer(),
                                   hash_bitmask=2 ** 20 - 1,
                                   filter=lambda x, y: True):
        """
        :param root_node: root root_node
        :param graph: graph
        :param radius_list:
        :param thickness_list:
        :param vectorizer: a vectorizer
        :param hash_bitmask:
        :return: radius_list*thicknes_list long list of cips
        """

        if not graphtools.filter(graph, root_node):
            return []
        if 'hlabel' not in graph.node[graph.nodes()[0]]:
            vectorizer._label_preprocessing(graph)

        # which nodes are in the relevant radius
        # print root_node,max(radius_list) + max(thickness_list)
        # myutils.display(graph,vertex_label='id',size=15)

        undir_graph = nx.Graph(graph)
        horizon = max(radius_list) + max(thickness_list)
        dist = nx.single_source_shortest_path_length(
            undir_graph, root_node, horizon)
        # we want the relevant subgraph and we want to work on a copy
        master_cip_graph = graph.subgraph(dist).copy()

        # we want to inverse the dictionary.
        # so now we see {distance:[list of nodes at that distance]}
        node_dict = graphtools.invert_dict(dist)

        cip_list = []
        for thickness_ in thickness_list:
            for radius_ in radius_list:

                # see if it is feasable to extract
                if radius_ + thickness_ not in node_dict:
                    continue

                # Calculation of core hash
                core_graph_nodes = [
                    item for x in range(radius_ + 1) for item in node_dict.get(x, [])]
                if not graphtools.filter(master_cip_graph, core_graph_nodes):
                    continue

                core_hash = graphtools.graph_hash2(
                    master_cip_graph.subgraph(core_graph_nodes), hash_bitmask)

                # Calculation of interface hash - remains unchanged
                interface_graph_nodes = [item for x in range(radius_ + 1, radius_ + thickness_ + 1)
                                         for item in node_dict.get(x, [])]
                for inode in interface_graph_nodes:
                    label = master_cip_graph.node[inode]['hlabel'][0]
                    master_cip_graph.node[inode][
                        'distance_dependent_label'] = label + dist[inode] - radius_
                subgraph = master_cip_graph.subgraph(interface_graph_nodes)
                interface_hash = graphtools.graph_hash(subgraph,
                                                       hash_bitmask,
                                                       node_name_label='distance_dependent_label')

                # get relevant subgraph
                nodes = [
                    node for i in range(radius_ + thickness_ + 1) for node in node_dict[i]]
                cip_graph = master_cip_graph.subgraph(nodes).copy()

                # marking cores and interfaces in subgraphs
                for i in range(radius_ + 1):
                    for no in node_dict[i]:
                        cip_graph.node[no]['core'] = True
                        if 'interface' in cip_graph.node[no]:
                            cip_graph.node[no].pop('interface')
                for i in range(radius_ + 1, radius_ + thickness_ + 1):
                    if i in node_dict:
                        for no in node_dict[i]:
                            cip_graph.node[no]['interface'] = True
                            if 'core' in cip_graph.node[no]:
                                cip_graph.node[no].pop('core')

                core_nodes_count = sum([len(node_dict[x])
                                        for x in range(radius_ + 1)])

                cip_list.append(CoreInterfacePair(interface_hash,
                                                  core_hash,
                                                  cip_graph,
                                                  radius_,
                                                  thickness_,
                                                  core_nodes_count,
                                                  distance_dict=node_dict))
        return cip_list
