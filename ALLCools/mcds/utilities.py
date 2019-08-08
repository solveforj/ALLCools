import logging
import numpy as np
import pandas as pd
import xarray as xr

log = logging.getLogger()


def calculate_posterior_mc_rate(mc_da,
                                cov_da,
                                var_dim,
                                normalize_per_cell=True,
                                clip_norm_value=10):
    # TODO add a parameter weighting var to adjust prior
    # so we can do post_rate only in a very small set of gene to prevent memory issue

    raw_rate = mc_da / cov_da
    cell_rate_mean = raw_rate.mean(dim=var_dim)  # this skip na
    cell_rate_var = raw_rate.var(dim=var_dim)  # this skip na

    # based on beta distribution mean, var
    # a / (a + b) = cell_rate_mean
    # a * b / ((a + b) ^ 2 * (a + b + 1)) = cell_rate_var
    # calculate alpha beta value for each cell
    cell_a = (1 - cell_rate_mean) * (cell_rate_mean ** 2) / cell_rate_var - cell_rate_mean
    cell_b = cell_a * (1 / cell_rate_mean - 1)

    # cell specific posterior rate
    post_rate = (mc_da + cell_a) / (cov_da + cell_a + cell_b)

    if normalize_per_cell:
        # there are two ways of normalizing per cell, by posterior or prior mean:
        # prior_mean = cell_a / (cell_a + cell_b)
        # posterior_mean = post_rate.mean(dim=var_dim)

        # Here I choose to use prior_mean to normalize cell,
        # therefore all cov == 0 features will have normalized rate == 1 in all cells.
        # i.e. 0 cov feature will provide no info
        prior_mean = cell_a / (cell_a + cell_b)
        post_rate = post_rate / prior_mean
        if clip_norm_value is not None:
            post_rate = post_rate.where(post_rate < clip_norm_value, clip_norm_value)
    return post_rate


def calculate_posterior_mc_rate_lazy(mc_da, cov_da, var_dim, output_prefix, cell_chunk=20000,
                                     normalize_per_cell=True, clip_norm_value=10):
    """
    Running calculate_posterior_mc_rate with dask array and directly save to disk.
    This is highly memory efficient. Use this for dataset larger then machine memory.

    Parameters
    ----------
    mc_da
    cov_da
    var_dim
    output_prefix
    cell_chunk
    normalize_per_cell
    clip_norm_value

    Returns
    -------

    """
    cell_list = mc_da.get_index('cell')
    cell_chunks = [cell_list[i:i + cell_chunk] for i in range(0, cell_list.size, cell_chunk)]

    output_paths = []
    for chunk_id, cell_list_chunk in enumerate(cell_chunks):
        _mc_da = mc_da.sel(cell=cell_list_chunk)
        _cov_da = cov_da.sel(cell=cell_list_chunk)
        post_rate = calculate_posterior_mc_rate(mc_da=_mc_da,
                                                cov_da=_cov_da,
                                                var_dim=var_dim,
                                                normalize_per_cell=normalize_per_cell,
                                                clip_norm_value=clip_norm_value)
        if len(cell_chunks) == 1:
            chunk_id = ''
        else:
            chunk_id = f'.{chunk_id}'
        output_path = output_prefix + f'.{var_dim}_da_rate{chunk_id}.mcds'

        # to_netcdf trigger the dask computation, and save output directly into disk, quite memory efficient
        post_rate.to_netcdf(output_path)
        output_paths.append(output_path)

    chunks = {'cell': mc_da.chunks['cell']}
    total_post_rate = xr.concat([xr.open_dataarray(path, chunks=chunks)
                                 for path in output_paths], dim='cell')
    return total_post_rate


def calculate_gch_rate(mcds, var_dim='chrom100k'):
    rate_da = mcds.sel(mc_type=['GCHN', 'HCHN']).add_mc_rate(dim=var_dim, da=f'{var_dim}_da',
                                                             normalize_per_cell=False, inplace=False)
    # (PCG - PCH) / (1 - PCH)
    real_gc_rate = (rate_da.sel(mc_type='GCHN') - rate_da.sel(mc_type='HCHN')) / (
            1 - rate_da.sel(mc_type='HCHN'))
    real_gc_rate = real_gc_rate.transpose('cell', var_dim).values
    real_gc_rate[real_gc_rate < 0] = 0

    # norm per cell
    cell_overall_count = mcds[f'{var_dim}_da'].sel(mc_type=['GCHN', 'HCHN']).sum(dim=var_dim)
    cell_overall_rate = cell_overall_count.sel(count_type='mc') / cell_overall_count.sel(count_type='cov')
    gchn = cell_overall_rate.sel(mc_type='GCHN')
    hchn = cell_overall_rate.sel(mc_type='HCHN')
    overall_gchn = (gchn - hchn) / (1 - hchn)
    real_gc_rate = real_gc_rate / overall_gchn.values[:, None]
    return real_gc_rate


def get_mean_dispersion(x, obs_dim):
    # mean
    mean = x.mean(dim=obs_dim)

    # var
    mean_sq = (x * x).mean(dim=obs_dim)
    # enforce R convention (unbiased estimator) for variance
    var = (mean_sq - mean ** 2) * (x.sizes[obs_dim] / (x.sizes[obs_dim] - 1))

    # now actually compute the dispersion
    mean.where(mean > 1e-12, 1e-12)  # set entries equal to zero to small value
    # raw dispersion is the variance normalized by mean
    dispersion = var / mean

    mean.compute()
    dispersion.compute()
    return mean, dispersion


def highly_variable_methylation_feature(
        cell_by_feature_matrix, feature_mean_cov,
        obs_dim=None, var_dim=None,
        min_disp=0.5, max_disp=None,
        min_mean=0, max_mean=5,
        n_top_feature=None, bin_min_features=5,
        mean_binsize=0.05, cov_binsize=100):
    """
    Adapted from Scanpy, see license above
    The main difference is that, this function normalize dispersion based on both mean and cov bins.
    """
    # RNA is only scaled by mean, but methylation is scaled by both mean and cov
    log.info('extracting highly variable features')

    if n_top_feature is not None:
        log.info('If you pass `n_top_feature`, all cutoffs are ignored.')

    # warning for extremely low cov
    low_cov_portion = (feature_mean_cov < 30).sum() / feature_mean_cov.size
    if low_cov_portion > 0.2:
        log.warning(f'{int(low_cov_portion * 100)}% feature with < 10 mean cov, '
                    f'consider filter by cov before find highly variable feature. '
                    f'Otherwise some low coverage feature may be elevated after normalization.')

    if len(cell_by_feature_matrix.dims) != 2:
        raise ValueError(f'Input cell_by_feature_matrix is not 2-D matrix, '
                         f'got {len(cell_by_feature_matrix.dims)} dim(s)')
    else:
        if (obs_dim is None) or (var_dim is None):
            obs_dim, var_dim = cell_by_feature_matrix.dims

    # rename variable
    x = cell_by_feature_matrix
    cov = feature_mean_cov

    mean, dispersion = get_mean_dispersion(x, obs_dim=obs_dim)
    dispersion = np.log(dispersion)

    # all of the following quantities are "per-feature" here
    df = pd.DataFrame(index=cell_by_feature_matrix.get_index(var_dim))
    df['mean'] = mean.to_pandas()
    df['dispersion'] = dispersion.to_pandas()
    df['cov'] = cov

    # instead of n_bins, use bin_size, because cov and mc are in different scale
    df['mean_bin'] = (df['mean'] / mean_binsize).astype(int)
    df['cov_bin'] = (df['cov'] / cov_binsize).astype(int)

    # save bin_count df, gather bins with more than bin_min_features features
    bin_count = df.groupby(['mean_bin', 'cov_bin']) \
        .apply(lambda i: i.shape[0]) \
        .reset_index() \
        .sort_values(0, ascending=False)
    bin_count.head()
    bin_more_than = bin_count[bin_count[0] > bin_min_features]
    if bin_more_than.shape[0] == 0:
        raise ValueError(f'No bin have more than {bin_min_features} features, uss larger bin size.')

    # for those bin have too less features, merge them with closest bin in manhattan distance
    # this usually don't cause much difference (a few hundred features), but the scatter plot will look more nature
    index_map = {}
    for _, (mean_id, cov_id, count) in bin_count.iterrows():
        if count > 1:
            index_map[(mean_id, cov_id)] = (mean_id, cov_id)
        manhattan_dist = (bin_more_than['mean_bin'] - mean_id).abs() + (bin_more_than['cov_bin'] - cov_id).abs()
        closest_more_than = manhattan_dist.sort_values().index[0]
        closest = bin_more_than.loc[closest_more_than]
        index_map[(mean_id, cov_id)] = tuple(closest.tolist()[:2])
    # apply index_map to original df
    raw_bin = df[['mean_bin', 'cov_bin']].set_index(['mean_bin', 'cov_bin'])
    raw_bin['use_mean'] = pd.Series(index_map).apply(lambda i: i[0])
    raw_bin['use_cov'] = pd.Series(index_map).apply(lambda i: i[1])
    df['mean_bin'] = raw_bin['use_mean'].values
    df['cov_bin'] = raw_bin['use_cov'].values

    # calculate bin mean and std, now disp_std_bin shouldn't have NAs
    disp_grouped = df.groupby(['mean_bin', 'cov_bin'])['dispersion']
    disp_mean_bin = disp_grouped.mean()
    disp_std_bin = disp_grouped.std(ddof=1)

    # actually do the normalization
    _mean_norm = disp_mean_bin.loc[list(zip(df['mean_bin'], df['cov_bin']))]
    _std_norm = disp_std_bin.loc[list(zip(df['mean_bin'], df['cov_bin']))]
    df['dispersion_norm'] = (df['dispersion'].values  # use values here as index differs
                             - _mean_norm.values) / _std_norm.values
    dispersion_norm = df['dispersion_norm'].values.astype('float32')

    # Select n_top_feature
    if n_top_feature is not None:
        dispersion_norm = dispersion_norm[~np.isnan(dispersion_norm)]
        dispersion_norm[::-1].sort()  # interestingly, np.argpartition is slightly slower
        disp_cut_off = dispersion_norm[n_top_feature - 1]
        gene_subset = np.nan_to_num(df['dispersion_norm'].values) >= disp_cut_off
        log.info(f'the {n_top_feature} top genes correspond to a normalized dispersion cutoff of {disp_cut_off}')
    else:
        max_disp = np.inf if max_disp is None else max_disp
        dispersion_norm[np.isnan(dispersion_norm)] = 0  # similar to Seurat
        gene_subset = np.logical_and.reduce((mean > min_mean, mean < max_mean,
                                             dispersion_norm > min_disp,
                                             dispersion_norm < max_disp))
    df['gene_subset'] = gene_subset
    log.info('    finished')
    return df
