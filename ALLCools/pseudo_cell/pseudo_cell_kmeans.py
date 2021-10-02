import pandas as pd
import numpy as np
from sklearn.cluster import MiniBatchKMeans
from scipy.sparse import issparse, csr_matrix, vstack
import anndata

def _kmeans_division(matrix, cells, max_pseudo_size, max_k=50):
    if max_pseudo_size <= 1:
        return '|'+pd.Series(range(cells.size), index=cells).astype(str)
    labels = pd.Series(index=cells)
    to_process = [(cells,'')]
    while len(to_process)>0:
        curr_cells, curr_prefix = to_process.pop()
        curr_matrix = matrix[cells.isin(curr_cells)]

        # MiniBatchKMeans causes seg fault with huge k; bound with max_k
        k = min(len(curr_cells)//max_pseudo_size+1, max_k)

        mbk = MiniBatchKMeans(n_clusters=k,
                              init='k-means++',
                              max_iter=100,
                              batch_size=100,
                              verbose=0,
                              compute_labels=True,
                              random_state=0,
                              tol=0.0,
                              max_no_improvement=10,
                              init_size=3 * k,
                              n_init=5,
                              reassignment_ratio=0.1)

        mbk.fit(curr_matrix)
        curr_labels = curr_prefix+'|'+pd.Series(mbk.labels_).astype(str)
        labels.loc[curr_cells] = curr_labels.tolist()
        curr_labels = labels.loc[curr_cells]

        for cluster_label, cluster_cells in curr_labels.groupby(curr_labels):
            if cluster_cells.size <= max_pseudo_size:
                continue
            else:
                to_process.append((cluster_cells.index, cluster_label))
    return labels


def _calculate_pseudo_group(clusters, total_matrix, pseudoable_cluster_size=100, max_pseudo_size=25):
    cluster_cells = clusters.value_counts()
    max_pseudo_sizes = (cluster_cells // pseudoable_cluster_size + 1).astype(int)
    max_pseudo_sizes[max_pseudo_sizes > max_pseudo_size] = max_pseudo_size

    records = []
    for cluster, max_pseudo_size in max_pseudo_sizes.items():
        cells = clusters[clusters == cluster].index
        matrix = total_matrix[clusters == cluster].copy()
        record = _kmeans_division(matrix, cells, max_pseudo_size)
        record = record.apply(lambda i: f'{cluster}::{i}')
        records.append(record)
    records = pd.concat(records)
    return records

def _merge_pseudo_cell(adata, aggregate_func, pseudo_group_key):
    is_sparse = issparse(adata.X)
    # merge ia to balanced ia (bia)
    balanced_matrix = []
    obs = {}
    for group, cells in adata.obs_names.groupby(adata.obs[pseudo_group_key]).items():
        n_cells = cells.size
        if cells.size == 1:
            group_data = adata.var_vector(cells[0]).ravel()
        else:
            if aggregate_func == 'sum':
                try:
                    group_data = adata[cells, :].X.sum(axis=0).A1
                except AttributeError:
                    group_data = np.array(adata[cells, :].X.sum(axis=0)).ravel()
            elif aggregate_func == 'mean':
                try:
                    group_data = adata[cells, :].X.mean(axis=0).A1
                except AttributeError:
                    group_data = np.array(adata[cells, :].X.mean(axis=0)).ravel()
            elif aggregate_func == 'median':
                try:
                    group_data = adata[cells, :].X.median(axis=0).A1
                except AttributeError:
                    group_data = np.array(adata[cells, :].X.median(axis=0)).ravel()
            elif aggregate_func == 'downsample':
                cell = np.random.choice(cells, 1)
                try:
                    group_data = adata[cell, :].X.sum(axis=0).A1  # just to reduce dim
                except AttributeError:
                    group_data = np.array(adata[cell, :].X).ravel()
            else:
                raise ValueError(f'aggregate_func can only be ["sum", "mean", "median"], got "{aggregate_func}"')
        balanced_matrix.append(csr_matrix(group_data))
        obs[group] = {'n_cells': n_cells}
    balanced_matrix = vstack(balanced_matrix)
    if not is_sparse:
        balanced_matrix = balanced_matrix.toarray()

    pseudo_cell_adata = anndata.AnnData(balanced_matrix,
                                        obs=pd.DataFrame(obs).T,
                                        var=adata.var.copy())
    return pseudo_cell_adata



def generate_pseudo_cells(adata,
                          cluster_col='leiden',
                          obsm='X_pca',
                          pseudoable_cluster_size=100,
                          max_pseudo_size=25,
                          aggregate_func='downsample',):

    pseudo_group_key='pseudo_group'

    # determine cell group
    clusters = adata.obs[cluster_col]
    total_matrix = adata.obsm[obsm]
    pseudo_group = _calculate_pseudo_group(clusters=clusters,
                                           total_matrix=total_matrix,
                                           pseudoable_cluster_size=pseudoable_cluster_size,
                                           max_pseudo_size=max_pseudo_size)
    adata.obs[pseudo_group_key] = pseudo_group
    pseudo_cell_adata = _merge_pseudo_cell(adata=adata,
                                           aggregate_func=aggregate_func,
                                           pseudo_group_key=pseudo_group_key,)
    pseudo_cell_adata.obs[cluster_col] = pseudo_cell_adata.obs_names.str.split('::').str[:-1].str.join('::')
    return pseudo_cell_adata
