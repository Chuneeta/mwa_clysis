from mwa_qa.read_metafits import Metafits
from mwa_qa.read_calfits import CalFits
from mwa_qa import json_utils as ju
from collections import OrderedDict
from scipy.ndimage import gaussian_filter1d
from scipy.signal import savgol_filter
import numpy as np
import warnings


pol_dict = {'XX': 0, 'XY': 1, 'YX': 2, 'YY':	3}


class CalMetrics(object):
    def __init__(self, calfits_path, metafits_path, pol='X',
                 norm=True, ref_antenna=None):
        """
        Object that takes in .fits containing the calibration solutions
        file readable by astropy and initializes them as global
        varaibles
        - calfile:	.fits file containing the calibration solutions
        - metafits:	Metafits with extension *.metafits or _ppds.fits
        containing information
        - pol: 	Polarization, can be either 'X' or 'Y'. It should be
                specified so that information associated on an
                observation done with MWA with the given pol is
                provided. Default is X.
        - norm: Boolean, If True, the calibration solutions will be
                normlaized else unnormlaized solutions will be used.
                Default is set to False
        - reference_antenna:	Reference antenna number. If norm is True,
                        a reference antenna is require for normalization.
                        By default it uses the last antenna in the array.
                        If the last antenna is flagged, it will return
                        an error.
        """
        self.calfits_path = calfits_path
        self.metafits_path = metafits_path
        self.CalFits = CalFits(calfits_path,
                               pol=pol, norm=norm, ref_antenna=ref_antenna)
        self.MetaFits = Metafits(metafits_path, pol=pol)

    def variance_for_antpair(self, antpair):
        """
        Returns variance across frequency for the given tile pair
        - antpair:	Antenna pair or tuple of antenna numbers
                                e.g (102, 103)
        - norm:	Boolean, If True returns normalized gains
                        else unormalized gains. Default is set to True.
        """
        gain_pairs = self.CalFits.gains_for_antpair(antpair)
        return np.nanvar(gain_pairs, axis=1)

    def variance_for_baselines_less_than(self, uv_cut):
        """
        Returns bls shorter than the specified cut and the variances
        calculated across frequency for each of the antenna pair
        - uv_cut:	Baseline cut in metres, will use only baselines
        shorter than the given value- norm
        """
        baselines = self.MetaFits.baselines_less_than(uv_cut)
        _sh = self.CalFits.gain_array.shape
        variances = np.zeros((_sh[0], len(baselines), 4))
        for i, bl in enumerate(baselines):
            variances[:, i, :] = self.variance_for_antpair(bl)
        return variances

    def skewness_across_uvcut(self, uv_cut):
        """
        Evaluates the Pearson skewness 3 * (mean - median) / std across the
        variances averaged over baseliness shorter than the given
        uv length
        - uv_cut:	Baseline cut in metres, will use only baselines shorter
                    than the given value
        - norm:		Boolean, If True returns normalized gains else unormalized
                    gains. Default is set to True.
        """
        variances = self.variance_for_baselines_less_than(uv_cut)
        vmean = np.nanmean(variances, axis=1)
        vmedian = np.nanmedian(variances, axis=1)
        vstd = np.nanstd(variances, axis=1)
        skewness = 3 * (vmean - vmedian) / vstd
        return np.nanmax(skewness, axis=0)

    def unused_baselines_percent(self):
        inds_nan = np.where(np.isnan(self.CalFits.baseline_weights))[0]
        inds_wg0 = np.where(self.CalFits.baseline_weights == 0)[0]
        return (len(inds_nan) + len(inds_wg0)) / \
            len(self.CalFits.baseline_weights) * 100

    def unused_channels_percent(self):
        inds = np.where(np.array(self.CalFits.frequency_flags) == 1)[0]
        return len(inds) / len(self.CalFits.frequency_flags) * 100

    def unused_antennas_percent(self):
        inds = np.where(np.array(self.CalFits.antenna_flags) == 1)[0]
        return len(inds) / len(self.CalFits.antenna_flags) * 100

    def non_converging_percent(self):
        sh = self.CalFits.convergence.shape
        count = 0
        for t in range(sh[0]):
            inds = np.where(np.isnan(self.CalFits.convergence[t, :]))[0]
            count += len(inds)
        return count / (sh[0] * sh[1]) * 100

    def convergence_variance(self):
        return np.nanmax(np.nanvar(self.CalFits.convergence, axis=1))

    def receiver_metrics(self):
        # metrics based on antennas connected to receivers
        warnings.filterwarnings("ignore")
        pols = list(pol_dict.keys())
        ntimes = self.CalFits.Ntime
        receivers = np.unique(self.MetaFits.receiver_ids)
        nants = len(self.MetaFits.antenna_numbers_for_receiver(receivers[0]))
        rcv_chisq = np.zeros((len(receivers), nants, len(pols)))
        for i, r in enumerate(receivers):
            rcv_gains = self.CalFits.gains_for_receiver(self.metafits_path, r)
            rcv_amps = np.nanmean(np.abs(rcv_gains), axis=0)
            # ignoring zero division
            np.seterr(divide='ignore', invalid='ignore')
            rcv_amps_mean = np.nanmean(rcv_amps, axis=0)
            chisq = np.nansum(((rcv_amps - rcv_amps_mean) / rcv_amps), axis=1)
            rcv_chisq[i] = chisq
        return rcv_chisq

    def _initialize_metrics_dict(self):
        """
        Initializes the metric dictionary with some of the default
        parameters
        """
        self.metrics = OrderedDict()
        # assuming hyperdrive outputs 4 polarizations
        self.metrics['POLS'] = ['XX', 'YY']
        self.metrics['OBSID'] = self.CalFits.obsid
        self.metrics['UVCUT'] = self.CalFits.uvcut
        self.metrics['M_THRESH'] = self.CalFits.m_thresh
        if self.CalFits.norm:
            self.metrics['REF_ANTENNA'] = self.CalFits.reference_antenna
        self.metrics['NTIME'] = self.CalFits.Ntime
        self.metrics['START_FREQ'] = self.CalFits.frequency_array[0]
        self.metrics['CH_WIDTH'] = self.CalFits.frequency_array[1] - \
            self.CalFits.frequency_array[0]
        self.metrics['NCHAN'] = self.CalFits.Nchan
        self.metrics['ANTENNA'] = self.CalFits.antenna
        self.metrics['XX'] = OrderedDict()
        self.metrics['YY'] = OrderedDict()
        # NOTE:polynomial parameters - only the fitted
        # solutions will have poly metrics
        try:
            self.metrics['POLY_ORDER'] = self.CalFits.poly_order
            self.metrics['POLY_MSE'] = self.CalFits.poly_mse
        except AttributeError:
            pass

    def run_metrics(self, ant_threshold=10):
        self._initialize_metrics_dict()
        gains = self.CalFits.gain_array
        fft_gains = self.CalFits.fft_gains()
        # normalizing gains by median across antenna
        gain_amps = np.abs(gains)
        _sh = gain_amps.shape
        # metrics amplitude across frequency
        rms_amp_freq = np.sqrt(np.nanmean(gain_amps ** 2, axis=2) / _sh[2])
        # skewness
        skewness = self.skewness_across_uvcut(self.metrics['UVCUT'])
        # receiver metrics
        rcv_chisq = self.receiver_metrics()
        mrcv_chisq = np.nanmax(rcv_chisq, axis=1)
        vmrcv_chisq = np.nanvar(mrcv_chisq, axis=0)

        for p in ['XX', 'YY']:
            rms_amp_freq_p = np.nanmean(rms_amp_freq, axis=0)[:, pol_dict[p]]
            # calculating modified zscore
            rms_median = np.nanmedian(rms_amp_freq_p)
            rms_modz = (rms_amp_freq_p - rms_median) / \
                (np.nanmedian(np.abs(rms_amp_freq_p - rms_median)))
            # determining misbehaving antennas using modified z-score
            inds = np.where((rms_modz < -1 * ant_threshold)
                            | (rms_modz > ant_threshold))
            bad_ants1 = self.CalFits.antenna[inds[0]]
            # determining misbehaving antennas from FFT data
            fft_amps = np.abs(np.nanmean(
                fft_gains[:, :, :, pol_dict[p]], axis=0))
            fft_power = np.nansum(fft_amps, axis=1)
            fft_power_median = np.nanmedian(fft_power)
            fft_power_modz = (fft_power - fft_power_median) / \
                (np.nanmedian(np.abs(fft_power - fft_power_median)))
            inds = np.where((fft_power_modz < -1 * ant_threshold)
                            | (fft_power_modz > ant_threshold))
            bad_ants2 = self.CalFits.antenna[inds[0]]
            bad_ants = np.unique(bad_ants1.tolist() + bad_ants2.tolist())

            # writing to metrics dict
            self.metrics[p]['RMS'] = rms_amp_freq_p
            self.metrics[p]['RMS_MODZ'] = rms_modz
            self.metrics[p]['BAD_ANTS'] = bad_ants
            self.metrics[p]['SKEWNESS'] = skewness[pol_dict[p]]
            self.metrics[p]['DFFT_POWER'] = np.nansum(fft_amps)

        self.metrics['PERCENT_UNUSED_BLS'] = self.unused_baselines_percent()
        self.metrics['BAD_ANTS'] = np.unique(
            self.metrics['XX']['BAD_ANTS'].tolist() + self.metrics['XX']['BAD_ANTS'].tolist())
        self.metrics['PERCENT_NONCONVERGED_CHS'] = self.non_converging_percent()
        self.metrics['PERCENT_BAD_ANTS'] = len(
            self.metrics['BAD_ANTS']) / self.MetaFits.Nants * 100
        self.metrics['RMS_CONVERGENCE'] = np.sqrt(np.nanmean(
            self.CalFits.convergence ** 2) / len(self.CalFits.convergence))
        self.metrics['SKEWNESS'] = np.nanmax(
            [self.metrics['XX']['SKEWNESS'], self.metrics['YY']['SKEWNESS']])
        self.metrics['RECEIVER_VAR'] = np.nanmax(
            [vmrcv_chisq[pol_dict['XX']], vmrcv_chisq[pol_dict['YY']]])
        self.metrics['DFFT_POWER'] = np.nanmean([self.metrics['XX']['DFFT_POWER'],
                                                 self.metrics['YY']['DFFT_POWER']])

    def write_to(self, outfile=None):
        if outfile is None:
            outfile = self.calfits_path.replace('.fits', '_cal_metrics.json')
        ju.write_metrics(self.metrics, outfile)
