'''
this module has a somewhat strange flow.
The Experiment class reads bam files and from these raw data
it's really easy to create a dataframe and normalize on those values.

However, other parts of the code (maximum_segments) expects 
an object per bin. Therefore, an utility function coverts 
a df to bin objects. 

'''
import functools
import collections
import numpy as np
import logit
from logbook import Logger
import pandas as pa
import read_bam


import estimate

log = Logger(__name__)

class Experiment(object):
    '''
    classmethod load_experiment reads bam files.
    Instances holds count vectors for IP/INPUT for each chrom
    and knows the bin size (assumed to be fixed)

    The method as_data_frame returns a pandas data frame of
    formatted results. Notice the normalized argument to this
    method.
    '''

    @classmethod
    def load_experiment(cls, chromsizes_path, ip_bam_path, 
            input_bam_path, bin_size=1000, use_multiprocessing=True):
        chromsizes = cls.read_chrom_sizes(chromsizes_path)
        f = functools.partial(read_bam.read_bam_into_bins,
                            chromsizes, bin_size)
        if use_multiprocessing:
            import multiprocessing
            pool = multiprocessing.Pool(processes=2)
            # async makes keyboard interrupt work (and not stall)
            fmap = lambda g, xs: pool.map_async(g, xs).get(99999999)
        else:
            fmap = map
        log.notice('loading bam files')
        ipd, inputd = fmap(f, [ip_bam_path, input_bam_path])
        log.notice('done')
        return cls(ipd, inputd, bin_size)


    def __init__(self, ip_countd, input_countd, bin_size):
        'should not by instansiated by user-land code'
        self.ipd = ip_countd
        self.inputd = input_countd
        self.bin_size = bin_size

    def aggregate_bins(self, times_bin_size=None, new_bin_size=None):
        if times_bin_size is not None:
            n = int(times_bin_size)
            assert n > 0
            if n == 1:
                return self
        elif new_bin_size is not None:
            assert new_bin_size % self.bin_size == 0
            n = int(new_bin_size / self.bin_size)
        else:
            raise Exception("no new bin size given, check api.")
        aipd = read_bam.aggregate_every_n_bins(self.ipd, n)
        ainputd= read_bam.aggregate_every_n_bins(self.inputd, n)
        return Experiment(aipd, ainputd, self.bin_size * n)

    @classmethod
    def read_chrom_sizes(cls, chrom_size_filename):
      d = {}
      f = open(chrom_size_filename)
      for line in f:
        chrom, size = line.split()
        if chrom == 'chrom' and size == 'size':
          continue
        d[chrom] = int(size)
      f.close()
      return d

    @classmethod
    def normalize_df(cls, df):
        input_scale_factor = df.ip.sum() / float(df.input.sum())
        log.notice('normalizing input with scale factor: %.2f' % input_scale_factor)
        ndf = df.copy()
        ndf.input = df.input * input_scale_factor
        return ndf

    def as_data_frame(self, normalize=True):
        def chrom_to_df(chrom_name, ip_cnts, input_cnts, bin_size):
            assert len(ip_cnts) == len(input_cnts)
            d = collections.OrderedDict()
            d['chrom'] = chrom_name
            d['start'] = np.arange(len(ip_cnts)) * bin_size
            d['end'] = d['start'] + bin_size
            d['ip'] = ip_cnts
            d['input'] = input_cnts
            return pa.DataFrame(d)
        assert len(self.ipd) == len(self.inputd)
        df = pa.concat([chrom_to_df(c, self.ipd[c], 
            self.inputd[c], self.bin_size)
            for c in self.ipd],
            ignore_index=True)
        if normalize:
            return self.normalize_df(df)
        else:
            return df

    def write_ratios(self, ratio_file):
        log.notice('writing log ratios to %s' % ratio_file)
        df = self.as_data_frame(normalize=True)
        df['ratio'] = np.log(df.ip / df.input).replace(
                [np.inf, -np.inf], np.nan)
        rdf = df.dropna()
        rdf.to_csv(ratio_file, sep='\t', cols=['chrom', 'start', 'end', 'ratio'],
                header=False, index=False)


class BamLoader(object):

    def __init__(self, chrom_size_path, bin_size, neg_score_scale,
                 number_of_processes=4):
        self.chrom_size_path = chrom_size_path
        self.bin_size = bin_size
        self.neg_score_scale = neg_score_scale
        self.bin_size = bin_size
        self.number_of_processes = number_of_processes

    def load_bam(self, ip_name, ctrl_name):
        return Experiment.load_experiment(self.chrom_size_path, ip_name,
                ctrl_name, 1000 if self.bin_size is None else self.bin_size, 
                use_multiprocessing=True)

    def __add_bin_scores(self, r1, r2):
        assert len(r1.index) == len(r2.index)
        assert (r1.index == r2.index).all()
        assert (r1.start == r2.start).all()
        common = r1.copy()
        common.score += r2.score
        return common

    def __load_experiment(self, ip_name, ctrl_name):
        exp = self.load_bam(ip_name, ctrl_name)
        if self.bin_size is None:
            self.bin_size = estimate.bin_size(exp)
            log.notice('Optimal bin size: %d' % self.bin_size)
        else:
            log.notice('Using preset bin size for %s and %s: %d' % (
                ip_name, ctrl_name, self.bin_size))
        odf = exp.aggregate_bins(new_bin_size=self.bin_size).as_data_frame()
        return logit.ci_for_df(odf)
        
    def load_single_experiment(self, ip_name, ctrl_name):
        self.df = self.__load_experiment(ip_name, ctrl_name)

    def load_multiple_experiments(self, ip_names, ctrl_names, which_merge_method='median'):
        assert self.bin_size is not None
        assert len(ip_names) == len(ctrl_names)
        scores = []
        for ip_name, ctrl_name in zip(ip_names, ctrl_names):
            x = self.__load_experiment(ip_name, ctrl_name)
            scores.append(np.array(x.score))
        df = x['chrom start end'.split()].copy()
        scores = pa.DataFrame(np.array(scores).transpose())
        # TODO support mean, sum and normalized sum in addition to
        # median
        log.info('merging replicate experiments using method: %s' % which_merge_method)
        if which_merge_method == 'median':
            df['score'] = scores.median(axis=1).values
        elif which_merge_method == 'sum':
            df['score'] = scores.sum(axis=1).values
        elif which_merge_method == 'normalized-sum':
            sums = scores.dropna().abs().sum(axis=0)
            norm_factors = sums / sums.min()
            df['score'] = (scores / norm_factors).sum(axis=1).values
        else:
            raise ArgumentError('%s is an illegal value for argument `which_merge_method`' % which_merge_method)
        self.df = df

    def get_df(self, unalignable_regions):
        if self.neg_score_scale is None:
            # TODO move this somewhere.
            # does not fit with rest of function
            log.notice('Estimating gap penalty')
            binscore_df = logit.extrapolate_low_info_bins(self.df)
            gpe = estimate.GapPenalty.instantiate(
                binscore_df, self.number_of_processes, unalignable_regions,
                mc_trials=100, pval_lim=0.05)
            self.neg_score_scale = gpe.search()
            gpe.cleanup()
            log.notice('Gap penalty estimated to %.1f' % self.neg_score_scale)
        
        df = logit.neg_score_scale(self.df, self.neg_score_scale)
        return logit.extrapolate_low_info_bins(df)

