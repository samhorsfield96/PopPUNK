#!/usr/bin/env python
# vim: set fileencoding=<utf-8> :
# Copyright 2018-2021 John Lees and Nick Croucher

# universal
import os
import sys

import pickle
import re
import pandas as pd
from scipy import sparse

# import poppunk package
from .__init__ import __version__

from .network import constructNetwork, generate_minimum_spanning_tree
from .plot import drawMST
from .trees import mst_to_phylogeny, write_tree
from .utils import setGtThreads, readIsolateTypeFromCsv

# command line parsing
def get_options():

    import argparse

    parser = argparse.ArgumentParser(description='Create a minimum-spanning tree',
                                     prog='poppunk_mst')

    # input options
    iGroup = parser.add_argument_group('Input files')
    iGroup.add_argument('--rank-fit', required=True, help='Location of rank fit, a sparse matrix (*_rank*_fit.npz)')
    iGroup.add_argument('--previous-clustering', help='CSV file with cluster definitions')
    iGroup.add_argument('--distance-pkl', help='Input pickle from distances, which contains sample names')
    iGroup.add_argument('--display-cluster', default=None, help='Column of clustering CSV to use for plotting')

    # output options
    oGroup = parser.add_argument_group('Output options')
    oGroup.add_argument('--output', required=True, help='Prefix for output files (required)')
    oGroup.add_argument('--no-plot', default=False, action='store_true',
                        help='Do not try and draw the MST')

    # processing
    other = parser.add_argument_group('Other options')
    other.add_argument('--threads', default=1, type=int,
                       help='Number of threads to use [default = 1]')
    other.add_argument('--gpu-graph', default=False, action='store_true',
                       help='Use cugraph for the calculation')

    other.add_argument('--version', action='version',
                       version='%(prog)s '+__version__)

    return parser.parse_args()

def main():

    # Check input args ok
    args = get_options()

    import graph_tool.all as gt
    try:
        import cugraph
        import cudf
    except ImportError as e:
        if args.gpu_graph:
            sys.stderr.write("cugraph and cudf unavailable\n")
            raise ImportError(e)

    # Read in sample names
    if (args.distance_pkl is not None) ^ (args.previous_clustering is not None):
        sys.stderr.write("To label strains, both --distance-pkl and --previous-clustering"
                         " must be provided\n")
        sys.exit(1)
    elif os.path.exists(args.distance_pkl):
        with open(args.distance_pkl, 'rb') as pickle_file:
            rlist, qlist, self = pickle.load(pickle_file)
            if not self:
                sys.stderr.write("This script must be run on a full all-v-all model\n")
                sys.exit(1)

    # Check output path ok
    if not os.path.isdir(args.output):
        try:
            os.makedirs(args.output)
        except OSError:
            sys.stderr.write("Cannot create output directory\n")
            sys.exit(1)
    setGtThreads(args.threads)

    # Create network with sparse dists
    sys.stderr.write("Loading distances into graph\n")
    sparse_mat = sparse.load_npz(args.rank_fit)
    if args.gpu_graph:
        G_df = cudf.DataFrame({'source': sparse_mat.row,
                               'destination': sparse_mat.col,
                               'weights': sparse_mat.data})
        G_cu = cugraph.Graph()
        G_cu.from_cudf_edgelist(G_df, edge_attr='weights', renumber=False)

        # Generate minimum spanning tree
        sys.stderr.write("Calculating MST (GPU part)\n")
        G_mst = cugraph.minimum_spanning_tree(G_cu, weight='weights')
        edge_df = G_mst.view_edge_list()
        sys.stderr.write("Calculating MST (CPU part)\n")
        edge_tuple = edge_df[['src', 'dst']].values
        G = constructNetwork(rlist, rlist,
                               edge_tuple,
                               0, edge_list=True,
                               weights=edge_df['weights'].values_host,
                               summarise=False)
    else:
        G = constructNetwork(rlist, rlist, None, 0,
                             sparse_input=sparse_mat, summarise=False)
        sys.stderr.write("Calculating MST (CPU)\n")

    mst = generate_minimum_spanning_tree(G, args.gpu_graph)

    # Save output
    sys.stderr.write("Generating output\n")
    mst.save(args.output + "/" + os.path.basename(args.output) + ".graphml", fmt="graphml")
    mst_as_tree = mst_to_phylogeny(mst, rlist)
    write_tree(mst_as_tree, args.output, "_MST.nwk", overwrite = True)

    # Make plots
    if not args.no_plot:
        if args.previous_clustering != None:
            mode = "clusters"
            if args.previous_clustering.endswith('_lineages.csv'):
                mode = "lineages"
            isolateClustering = readIsolateTypeFromCsv(args.previous_clustering,
                                                       mode = mode,
                                                       return_dict = True)
        else:
            # Create dictionary with everything in the same cluster if none passed
            isolateClustering = {'Cluster': {}}
            for v in mst.vertices:
                isolateClustering['Cluster'][mst.vp.id[v]] = '0'

        # Check selecting clustering type is in CSV
        clustering_name = 'Cluster'
        if args.display_cluster != None and args.previous_clustering != None:
            if args.display_cluster not in isolateClustering.keys():
                sys.stderr.write('Unable to find clustering column ' + args.display_cluster + ' in file ' +
                                 args.previous_clustering + '\n')
                sys.exit()
            else:
                clustering_name = args.display_cluster
        else:
            clustering_name = list(isolateClustering.keys())[0]

        # Draw MST
        drawMST(mst, args.output, isolateClustering, clustering_name, True)

    sys.exit(0)

