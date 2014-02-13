import numpy as np
from logbook import Logger
log = Logger(__name__)

def logit(xs):
    return np.log(xs) - np.log(1 - xs)

def get_medians(df):
    neg = np.median(df.ix[df.score < 0].score)
    pos = np.median(df.ix[df.score > 0].score)
    return neg, pos

def get_ci_intervals(p, tot_reads):
    z = 1.96
    const1 = p + (z**2)/(2*tot_reads)
    const2 = z * np.sqrt((p * (1-p) + z**2 / (4 * tot_reads)) / tot_reads)
    divisor = 1 +  z**2 / tot_reads
    ci_low = (const1 - const2) / divisor
    ci_high = (const1 + const2) / divisor
    return ci_low, ci_high


def ci_for_df(odf, ci_min=0.25, pscore_lim=10):
    df = odf.copy()
    df['tot_reads'] = df.ip + df.input
    df['avg'] = df.ip / df.tot_reads.astype(float)
    df['ci_low'], df['ci_high'] = get_ci_intervals(df.avg, df.tot_reads)
    df['ci_diff'] = df.ci_high - df.ci_low
    # we assume that the sample mean is normally distributed
    # if equation below is > pscore_lim. If so, we can compute the
    # 95% confidence interval
    normal_sample_mean = np.minimum(df.avg, 1 - df.avg) * df.tot_reads > pscore_lim
    small_CI = df.ci_diff < ci_min
    scorable_bins = np.logical_and(normal_sample_mean, small_CI)
    df['score'] = logit(df.ix[scorable_bins].avg)
    return df

def get_nib_ratio(df):
    nbins_with_reads = (df.tot_reads > 0).sum()
    nbins_ok = len(df.score.dropna())
    return float(nbins_with_reads - nbins_ok) / nbins_with_reads

def extrapolate_low_info_bins(odf):
    df = odf.copy()
    median_neg, median_pos = get_medians(df.dropna())
    df.ix[np.isnan(df.score), 'score'] = median_neg
    return df
    
def neg_score_scale(odf, scale):
    df = odf.copy()
    df.ix[df.score < 0, 'score'] *= scale
    return df
