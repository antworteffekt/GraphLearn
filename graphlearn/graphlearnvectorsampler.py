import estimatorwrapper
# from graphtools import extract_core_and_interface, core_substitution, graph_clean, mark_median
from graphlearn import GraphLearnSampler
from lsgvectorlabels import LSGVectorLabels
from eden.graph import Vectorizer
# from eden.util import serialize_dict
import logging
import postprocessing

logger = logging.getLogger(__name__)


class GraphLearnVectorSampler(GraphLearnSampler):

    def __init__(self,
                 nbit=20,
                 complexity=3,
                 vectorizer=Vectorizer(complexity=3),
                 random_state=None,
                 estimator=estimatorwrapper.EstimatorWrapper(),
                 postprocessor=postprocessing.PostProcessor(),
                 radius_list=[0, 1],
                 thickness_list=[1, 2],
                 node_entity_check=lambda x, y: True,
                 grammar=None,
                 min_cip_count=2,
                 min_interface_count=2):

        GraphLearnSampler.__init__(
            self, nbit, complexity, vectorizer, random_state, estimator,
            postprocessor, radius_list, thickness_list, node_entity_check, grammar)

        if grammar is None:
            self.lsgg = \
                LSGVectorLabels(self.radius_list,
                                self.thickness_list,
                                vectorizer=self.vectorizer,
                                min_cip_count=min_cip_count,
                                min_interface_count=min_interface_count,
                                nbit=self.nbit,
                                node_entity_check=self.node_entity_check)
        else:
            self.lsgg = grammar
