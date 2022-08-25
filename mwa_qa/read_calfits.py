from mwa_qa.read_metafits import Metafits
from scipy import signal
from astropy.io import fits
import numpy as np
import copy


def hdu_fields(hdr):
    fields = []
    try:
        nfield = hdr['TFIELDS']
        for fd in range(1, nfield + 1):
            fields.append(hdr['TTYPE{}'.format(fd)])
    except KeyError:
        print('WARNING: No fields is found for HDU '
              'Column "{}"'.format(hdu_name))
        pass
    return tuple(fields)


class CalFits(object):
    def __init__(self, calfits_path, metafits_path=None, pol='X',
                 norm=False, ref_antenna=None):
        """
        Object takes in a calfile in fits format and extracts
        bit and pieces of the required informations
        - calfits_path:	Fits file readable by astropy containing
                    calibration solutions (support for hyperdrive
                    output only for now) and related information
        - metafits_path:	Metafits with extension *.metafits containing
                    information corresponding to the observation
                        for which the calibration solutions is derived
        - pol:	Polarization, can be either 'X' or 'Y'. It should be
                specified so that information associated
                with the given pol is provided. Default is 'X'
        - norm: Boolean, If True, the calibration solutions will be
                normlaized else unnormlaized solutions will be used.
                Default is set to False
        - ref_antenna:   Reference antenna number. If norm is True,
                        a reference antenna is require for normalization.
                        By default it uses the last antenna in the array.
                        If the last antenna is flagged, it will return
                        an error.
        """
        self.calfits_path = calfits_path
        self.metafits_path = metafits_path
        with fits.open(calfits_path) as hdus:
            cal_hdu = hdus['SOLUTIONS']
            time_hdu = hdus['TIMEBLOCKS']
            tile_hdu = hdus['TILES']
            chan_hdu = hdus['CHANBLOCKS']
            result_hdu = hdus['RESULTS']
            bls_hdu = hdus['BASELINES']

            self.gain_array = cal_hdu.data[:, :, :, ::2] + \
                1j * cal_hdu.data[:, :, :, 1::2]
            self.start_time = time_hdu.data[0][0]
            self.end_time = time_hdu.data[0][1]
            self.average_time = time_hdu.data[0][2]
            self.Ntime = len(self.gain_array)
            self.uvcut = hdus[0].header['UVW_MIN']
            self.obsid = hdus[0].header['OBSID']
            tile_fields = hdu_fields(tile_hdu.header)
            if 'Antenna' in tile_fields:
                self.antenna = [tile_hdu.data[i][0]
                                for i in range(len(tile_hdu.data))]
            if 'TileName' in tile_fields:
                self.tilename = [tile_hdu.data[i][1]
                                 for i in range(len(tile_hdu.data))]
            if 'Flag' in tile_fields:
                self.antenna_flags = [tile_hdu.data[i][2]
                                      for i in range(len(tile_hdu.data))]
            chan_fields = hdu_fields(chan_hdu.header)
            if 'Index' in chan_fields:
                self.frequency_channels = [
                    chan_hdu.data[i][0] for i in range(len(chan_hdu.data))]
            if 'Freq' in chan_fields:
                self.frequency_array = [chan_hdu.data[i][1]
                                        for i in range(len(chan_hdu.data))]
            if 'Flag' in chan_fields:
                self.frequency_flags = [chan_hdu.data[i][2]
                                        for i in range(len(chan_hdu.data))]
            self.convergence = result_hdu.data
            self.baseline_weights = bls_hdu.data
            self.norm = norm
            if self.norm:
                if ref_antenna is None:
                    ref_antenna = self._iterate_refant()
                    self.ref_antenna = ref_antenna
                else:
                    self.ref_antenna = ref_antenna
                    self._check_refant()
                self.gain_array = self.normalized_gains()
            self.amplitudes = np.abs(self.gain_array)
            self.phases = np.angle(self.gain_array)
            self.Metafits = Metafits(metafits_path, pol=pol)

    def _check_refant(self):
        """
        Checks if the given reference antenna is flagged due to non-convergence
        or any malfunctioning reports
        """
        ind = self.gains_ind_for(self.ref_antenna)
        flag = np.array(self.antenna_flag)[ind]
        assert flag == 0,  "{} seems to be flagged."
        "calibration solutions found, choose a different tile"

    def _iterate_refant(self):
        anindex = -1
        while anindex < 0:
            if self.antenna_flags[anindex] == 0:
                break
            anindex -= 1
        return self.antenna[anindex]

    def gains_ind_for(self, antnum):
        """
        Returns index of the gain solutions fot the given antenna number,
                        indices matches the antenna number in this case
        - antnum : Antenna Number
        """
        return antnum

    def _normalized_data(self, data):
        """
        Normalizes the gain solutions for each timeblock given a reference tile
        - data:	Input array of shape( tiles, freq, pols) containing the
                        solutions
        """
        ref_ind = self.gains_ind_for(self.ref_antenna)
        refs = []
        for ref in data[ref_ind].reshape((-1, 2, 2)):
            refs.append(np.linalg.inv(ref))
        refs = np.array(refs)
        div_ref = []
        for tile_i in data:
            for (i, ref) in zip(tile_i, refs):
                div_ref.append(i.reshape((2, 2)).dot(ref))
        return np.array(div_ref).reshape(data.shape)

    def normalized_gains(self):
        """
        Returns the normalized gain solutions using the
        given reference Antenna number
        """
        ngains = copy.deepcopy(self.gain_array)
        for t in range(len(ngains)):
            ngains[t] = self._normalized_data(self.gain_array[t])
        return ngains

    def gains_for_antnum(self, antnum):
        """
        Returns gain solutions for the given tile ID
        - antnum:	Antenna Number, starts from 1
        - norm:	Boolean, If True returns normalized gains
                        else unormalized gains.
                        Default is set to True.
        """
        ind = self.gains_ind_for(antnum)
        return self.gain_array[:, ind, :, :]

    def gains_for_antpair(self, antpair):
        """
        Evaluates conjugation of the gain solutions for antenna pair
                        (tile0, tile1)
        - antpair:	Tuple of antenna numbers such as (1, 2)
        """
        gains_t0 = self.gains_for_antnum(antpair[0])
        gains_t1 = self.gains_for_antnum(antpair[1])
        return gains_t0 * np.conj(gains_t1)

    def gains_for_receiver(self, receiver):
        """
        Returns the dictionary of gains solutions for all the antennas
        (8 antennas in principles) connected to the given receiver
        """
        assert self.metafits_path is not None, "metafits file associated"
        "with this observation is required to extract the receiver information"
        annumbers = self.Metafits.annumbers_for_receiver(receiver)
        gains0 = self.gains_for_antnum(annumbers[0])
        _sh = gains0.shape
        gains_array = np.zeros(
            (_sh[0], len(annumbers), _sh[1], _sh[2]), dtype=gains0.dtype)
        for i, an in enumerate(annumbers):
            gains_array[:, i, :, :] = self.gains_for_antnum(an)
        return gains_array

    def blackmanharris(self, n):
        return signal.windows.blackmanharris(n)

    def delays(self):
        # Evaluates geometric delay (fourier conjugate of frequency)
        df = (self.frequency_array[1] - self.frequency_array[0]) * 1e-9
        delays = np.fft.fftfreq(len(self.frequency_array), df)
        return delays

    def _filter_nans(self, data):
        nonans_inds = np.where(~np.isnan(data))[0]
        nans_inds = np.where(np.isnan(data))[0]
        return nonans_inds, nans_inds

    def gains_fft(self):
        _sh = self.gain_array.shape
        fft_data = np.zeros(_sh, dtype=self.gain_array.dtype)
        window = self.blackmanharris(len(self.frequency_array))
        for t in range(_sh[0]):
            for i in range(_sh[1]):
                for j in range(_sh[3]):
                    try:
                        nonans_inds, nans_inds = self._filter_nans(
                            self.gain_array[t, i, :, j])
                        d_fft = np.fft.fft(
                            self.gain_array[t, i, nonans_inds, j] * window[nonans_inds])
                        fft_data[t, i, nonans_inds, j] = np.fft.fftshift(d_fft)
                        fft_data[t, i, nans_inds, j] = np.nan
                    except ValueError:
                        fft_data[t, i, :, j] = np.nan
        return fft_data
