"""
Abstracted processing device

Creates a common API for dynamically running on CPU, GPU, or many GPUs
"""
from __future__ import absolute_import, division, print_function
import ubelt as ub
import warnings
import torch
import six
import os


__all__ = ['XPU']

try:
    # minimum memory (MB) needed for auto to resolve to GPU by default
    NETHARN_MIN_MB = int(os.environ['NETHARN_MIN_MB'])
except Exception:
    NETHARN_MIN_MB = 6000


if torch.__version__.startswith('0.3'):
    _TENSOR_TYPES = (torch._TensorBase, torch.autograd.Variable)
else:
    _TENSOR_TYPES = (torch.Tensor, torch.autograd.Variable)


class MountedModel(torch.nn.Module):
    """
    Abstraction of DataParallel and DataSerial
    """
    pass


class DataParallel(torch.nn.DataParallel, MountedModel):
    """
    Hack to redefine DataParallel such that it shares a base with DataSerial
    """
    pass


class DataSerial(MountedModel):
    """
    Wraper to create consistent API with DataParallel
    """
    def __init__(self, module):
        super(DataSerial, self).__init__()
        self.module = module

    def forward(self, *inputs, **kwargs):
        return self.module.forward(*inputs, **kwargs)


class XPU(ub.NiceRepr):
    """
    A processing device or devices: either a CPU, GPU, or multiple GPUS.

    Args:
        item (None, int, or list): None for cpu, an int for a gpu, or a list of
            ints for multiple gpus.
    TODO:
        distributed processing

    CommandLine:
        python -m netharn.device XPU

    Example:
        >>> print(str(XPU(None)))
        CPU
        >>> print(str(XPU(0, check=False)))
        GPU(0)
        >>> print(str(XPU([1, 2, 3], check=False)))
        GPU(1*,2,3)
        >>> import pytest
        >>> with pytest.raises(IndexError):
        >>>     print(str(XPU([], check=False)))
    """
    def __init__(xpu, item=None, check=True):
        xpu._main_device_id = None
        xpu._device_ids = None
        xpu.mode = None

        # For context manager
        xpu._cuda_device = None

        if isinstance(item, torch.device):
            if item.type == 'cpu':
                item = None
            elif item.type == 'cuda':
                item = item.index or 0
            else:
                raise KeyError(item.type)
        elif isinstance(item, six.string_types):
            item = item.lower()
            item = item.replace('cpu', '')
            item = item.replace('gpu', '')
            item = item.replace('cuda', '')
            if ',' in item:
                item = list(map(int, ','.split(item)))
            if item == '':
                item = 0
            if item == 'none':
                item = None
            else:
                item = int(item)

        if check:
            if not XPU.exists(item):
                if isinstance(item, int) and not torch.cuda.is_available():
                    raise ValueError('XPU {} does not exist. '
                                     'CUDA is not available'.format(item))
                else:
                    raise ValueError('XPU {} does not exist.'.format(item))

        if item is None:
            xpu.mode = 'cpu'
        elif isinstance(item, int):
            xpu.mode = 'gpu'
            xpu._main_device_id = item
        elif isinstance(item, (list, tuple)):
            xpu.mode = 'multi-gpu'
            xpu._device_ids = list(item)
            if not xpu._device_ids:
                raise IndexError('empty device list')
            xpu._main_device_id = xpu._device_ids[0]
        else:
            raise TypeError(xpu)

        if xpu._main_device_id is not None:
            xpu._cuda_device = torch.cuda.device(xpu._main_device_id)

    @property
    def main_device(xpu):
        """
        Example:
            >>> xpu = XPU(None)
            >>> print(repr(xpu.main_device))
            device(type='cpu')
        """
        if xpu.is_gpu():
            return torch.device(type='cuda', index=xpu._main_device_id)
        else:
            return torch.device(type='cpu')

    @classmethod
    def exists(XPU, item):
        """
        Determins if GPU/CPU exists

        Args:
            item (int or None):
        """
        if item is None:
            return True
        elif isinstance(item, int):
            if item < 0:
                raise ValueError('gpu num must be positive not {}'.format(item))
            return (torch.cuda.is_available() and
                    item < torch.cuda.device_count())
        elif isinstance(item, (tuple, list)):
            return all(XPU.exists(i) for i in item)
        else:
            raise TypeError(type(item))

    @classmethod
    def of(XPU, item, **kwargs):
        """
        Creates an XPU to represent the processing device(s) a Module, Tensor,
        or Variable currently exists on.

        Example:
            >>> xpu = XPU.of(torch.randn(3))
            >>> assert not xpu.is_gpu()
            >>> if torch.cuda.is_available():
            >>>     xpu = XPU.of(torch.randn(3).to('cuda'))
            >>>     assert xpu.is_gpu()
            >>>     for i in range(torch.cuda.device_count()):
            >>>         xpu = XPU.of(torch.randn(3).to(i))
            >>>         assert xpu.is_gpu()
            >>>         assert xpu._main_device_id == i
        """
        if hasattr(item, 'device'):
            return XPU(item.device)
        if hasattr(item, 'is_cuda'):
            if item.is_cuda:
                return XPU(item.get_device())
            else:
                return XPU(None)
        elif hasattr(item, 'state_dict'):
            devices = [item.device for item in item.state_dict().values()]
            _device_ids = set()
            for device in devices:
                if device.type == 'cuda':
                    index = device.index or 0
                    _device_ids.add(index)
                else:
                    _device_ids.add(None)
            try:
                _device_ids = sorted(_device_ids)
            except TypeError:
                raise Exception('cannot currently mix CPU and GPU')
            return XPU(_device_ids)
        else:
            raise TypeError(type(item))

    from_data = of

    @classmethod
    def cast(xpu, item, **kwargs):
        """
        Converts objects of many different types into an XPU.

        Args:
            item : special string, int, list, or None
        """
        if item is None:
            return XPU(item)
        elif isinstance(item, XPU):
            return item
        elif isinstance(item, _TENSOR_TYPES):
            return XPU.of(item)
        elif isinstance(item, torch.nn.Module):
            return XPU.of(item)
        elif isinstance(item, int):
            return XPU(item)
        elif isinstance(item, (list, tuple)):
            return XPU(item)
        elif isinstance(item, six.string_types):
            if item == 'auto':
                return XPU.from_auto(**kwargs)
            elif item == 'argv':
                return XPU.from_argv(**kwargs)
            if item == 'cpu' or item is None:
                return XPU(None)
            elif item == 'cpu' or item is None:
                return XPU(None)
            else:
                item = item.lower()
                item = item.replace('cpu', '')
                item = item.replace('gpu', '')
                item = item.replace('cuda', '')
                if ',' in item:
                    item = list(map(int, ','.split(item)))
                if item == '':
                    if torch.cuda.is_available():
                        item = XPU.default_gpu()
                    else:
                        item = None
                if item == 'none':
                    item = None
                else:
                    item = int(item)
                return XPU(item)
        else:
            raise ValueError('cannot cast to XPU. item={}'.format(item))

    @classmethod
    def from_auto(XPU, min_memory=NETHARN_MIN_MB):
        """
        Determines what a CPU/GPU device to use.

        Args:
            min_memory (int): min memory needed in bytes to default to GPU.
                defaults to envvar NETHARN_MIN_MB or 6000
        """
        if torch.cuda.is_available():
            n_available = torch.cuda.device_count()
            gpu_num = find_unused_gpu(min_memory=min_memory)
            if gpu_num is None or gpu_num >= n_available:
                gpu_num = None
        else:
            gpu_num = None
        xpu = XPU(gpu_num)
        return xpu

    @classmethod
    def from_argv(XPU, **kwargs):
        """
        Respect command line gpu and cpu argument

        CommandLine:
            python -m netharn.device XPU.from_argv --gpu=0,1

        Example:
            >>> xpu = XPU.from_argv()
            >>> print(xpu)
        """
        anygpu = ub.argflag('--gpu')
        if anygpu:
            gpu_num = XPU.default_gpu()
        else:
            gpu_num = ub.argval('--gpu', default=None)
        if ub.argflag('--cpu'):
            xpu = XPU(None)
        elif gpu_num is None:
            xpu = XPU.from_auto(**kwargs)
        else:
            if gpu_num.lower() == 'none':
                xpu = XPU(None)
            if isinstance(gpu_num, six.string_types) and ',' in gpu_num:
                _device_ids = list(map(int, gpu_num.split(',')))
                xpu = XPU(_device_ids)
            else:
                xpu = XPU(int(gpu_num))
        return xpu

    def __str__(xpu):
        return xpu.__nice__()

    def __enter__(xpu):
        if xpu._cuda_device:
            xpu._cuda_device.__enter__()
        return xpu

    def __exit__(xpu, ex_type, ex_value, tb):
        if xpu._cuda_device:
            return xpu._cuda_device.__exit__(ex_type, ex_value, tb)

    def __nice__(xpu):
        if xpu.is_gpu():
            if xpu._device_ids:
                parts = [str(n) + '*' if n == xpu._main_device_id else str(n)
                         for n in xpu._device_ids]
                return 'GPU({})'.format(','.join(parts))
            else:
                return 'GPU({})'.format(xpu._main_device_id)
        else:
            return 'CPU'

    def __json__(xpu):
        """
        CommandLine:
            xdoctest -m ~/code/netharn/netharn/device.py XPU.__json__

        Example:
            >>> print(XPU(None).__json__())
            CPU
            >>> print(XPU(0, check=False).__json__())
            GPU(0)
            >>> print(XPU([1, 2, 3], check=False).__json__())
            GPU(1*,2,3)
        """
        return str(xpu)

    def __int__(xpu):
        return xpu._main_device_id

    def number_of_devices(xpu):
        """ The number of underlying devices abstracted by this XPU """
        return 1 if not xpu._device_ids else len(xpu._device_ids)

    def is_gpu(xpu):
        """ True if running in single or parallel gpu mode """
        return xpu._main_device_id is not None
        # return 'gpu' in xpu.mode

    def mount(xpu, model):
        """
        Like move, but only for models. Creates an instance


        Mounts a model on the xpu.
        (Note this may be multiple gpus).

        Unlike move this function does NOT work in place.

        Example:
            >>> model = torch.nn.Conv2d(1, 1, 1)
            >>> xpu = XPU()
        """
        if isinstance(model, (MountedModel, torch.nn.DataParallel)):
            # Unwrap the core model
            model = model.module

        model = xpu.move(model)
        if xpu._device_ids:
            model = DataParallel(model, device_ids=xpu._device_ids,
                                 output_device=xpu._main_device_id)
        else:
            model = DataSerial(model)
        return model

    def move(xpu, data, **kwargs):
        """
        Args:
            data (torch.Tensor): raw data
            **kwargs : forwarded to `data.cuda`

        Notes:
            this function operates inplace.

        Example:
            >>> data = torch.FloatTensor([0])
            >>> if torch.cuda.is_available():
            >>>     xpu = XPU.cast('gpu')
            >>>     assert isinstance(xpu.move(data), torch.cuda.FloatTensor)
            >>> xpu = XPU.cast('cpu')
            >>> assert isinstance(xpu.move(data), torch.FloatTensor)
        """
        if xpu.is_gpu():
            return data.to(xpu._main_device_id, **kwargs)
        else:
            return data.to('cpu')

    def variable(xpu, item, **kw):
        """
        Moves data to this XPU and wraps it inside a `torch.autograd.Variable`

        Args:
            item (Tensor): a of tensors
            **kwargs: forwarded to `xpu.move` and `torch.autograd.Variable`

        Returns:
            torch.autograd.Variable: variable on the xpu

        Example:
            >>> from netharn.device import *
            >>> xpu = XPU(None)
            >>> data = torch.FloatTensor([0])
            >>> vari = xpu.variable(data)
            >>> assert isinstance(vari, torch.autograd.Variable)
            >>> # Ensure this function is idempotent
            >>> vari2 = xpu.variable(vari)
        """
        assert 'volatile' not in kw, 'volatile is removed'
        cukw = {}
        if 'async' in kw:
            cukw['non_blocking'] = kw.pop('async')
        if 'non_blocking' in kw:
            cukw['non_blocking'] = kw.pop('non_blocking')
        if torch.__version__.startswith('0.3'):
            # Unwrap the data and make a new variable
            if isinstance(item, torch.autograd.Variable):
                item = item.data
        item = xpu.move(item, **cukw)
        item = torch.autograd.Variable(item, **kw)
        return item

    def variables(xpu, *args, **kw):
        """
        Convinience function to wrap multiple Tensors in Variables at once
        """
        for item in args:
            yield xpu.variable(item, **kw)

    @classmethod
    def default_gpu(XPU):
        """
        Example:
            >>> print(XPU.default_gpu())
        """
        if torch.cuda.is_available():
            return torch.cuda.current_device()
        else:
            return None

    def set_as_default(xpu):
        """
        Sets this device as the default torch GPU

        Example:
            >>> import pytest
            >>> XPU(None).set_as_default()
            >>> if torch.cuda.is_available():
            >>>     XPU(0).set_as_default()
            >>>     assert torch.cuda.current_device() == 0
        """
        if xpu.is_gpu():
            torch.cuda.set_device(xpu._main_device_id)
        else:
            torch.cuda.set_device(-1)

    def load(xpu, fpath):
        """
        Loads data from a filepath onto this XPU

        Args:
            fpath (str or file): path to torch data file or file-like object

        Example:
            >>> from os.path import join
            >>> dpath = ub.ensure_app_cache_dir('netharn')
            >>> fpath = join(dpath, 'foo.pt')
            >>> cpu = XPU(None)
            >>> data = torch.FloatTensor([0])
            >>> torch.save(data, fpath)
            >>> loaded = cpu.load(fpath)
            >>> assert all(data == loaded)
        """
        # print('Loading data onto {} from {}'.format(xpu, fpath))
        try:
            return torch.load(fpath, map_location=xpu._map_location)
        except Exception:
            print('XPU={} Failed to load fpath={}'.format(xpu, fpath))
            raise

    def _map_location(xpu, storage, location):
        """
        Helper for `xpu.load` used when calling `torch.load`

        Args:
            storage (torch.Storage) : the initial deserialization of the
                storage of the data read by `torch.load`, residing on the CPU.
            location (str): tag identifiying the location the data being read
                by `torch.load` was originally saved from.

        Returns:
            torch.Storage : the storage
        """
        if xpu.is_gpu():
            return storage.cuda(xpu._main_device_id)
        else:
            return storage

    def synchronize(xpu):
        """
        Should be used when benchmarking performance of GPU implementaions
        """
        if xpu.is_gpu():
            torch.cuda.synchronize()


def find_unused_gpu(min_memory=0):
    """
    Finds GPU with the lowest memory usage by parsing output of nvidia-smi

    Args:
        min_memory (int): disregards GPUs with fewer than `min_memory` free MB

    Returns:
        int or None: gpu num if a match is found otherwise None

    CommandLine:
        python -c "from netharn import device; print(device.find_unused_gpu(300))"

    Example:
        >>> if torch.cuda.is_available():
        >>>     item = find_unused_gpu()
        >>>     assert item is None or isinstance(item, int)
    """
    gpus = gpu_info()
    if not gpus:
        return None
    gpu_avail_mem = {n: gpu['mem_avail'] for n, gpu in gpus.items()}
    usage_order = ub.argsort(gpu_avail_mem)
    gpu_num = usage_order[-1]
    if gpu_avail_mem[gpu_num] < min_memory:
        return None
    else:
        return gpu_num


def gpu_info():
    """
    Run nvidia-smi and parse output

    Returns:
        OrderedDict: info about each GPU indexed by gpu number

    Note:
        Does not gaurentee CUDA is installed.

    Warnings:
        if nvidia-smi is not installed

    Example:
        >>> if torch.cuda.is_available():
        >>>     gpus = gpu_info()
        >>>     assert len(gpus) == torch.cuda.device_count()
    """
    try:
        result = ub.cmd('nvidia-smi')
        if result['ret'] != 0:
            warnings.warn('Problem running nvidia-smi.')
            return None
    except Exception:
        warnings.warn('Could not run nvidia-smi.')
        return {}

    lines = result['out'].splitlines()

    gpu_lines = []
    current = None

    for line in lines:
        if current is None:
            # Signals the start of GPU info
            if line.startswith('|====='):
                current = []
        else:
            if len(line.strip()) == 0:
                # End of GPU info
                break
            elif line.startswith('+----'):
                # Move to the next GPU
                gpu_lines.append(current)
                current = []
            else:
                current.append(line)

    def parse_gpu_lines(lines):
        line1 = lines[0]
        line2 = lines[1]
        gpu = {}
        gpu['name'] = ' '.join(line1.split('|')[1].split()[1:-1])
        gpu['num'] = int(' '.join(line1.split('|')[1].split()[0]))

        mempart = line2.split('|')[2].strip()
        part1, part2 = mempart.split('/')
        gpu['mem_used'] = float(part1.strip().replace('MiB', ''))
        gpu['mem_total'] = float(part2.strip().replace('MiB', ''))
        gpu['mem_avail'] = gpu['mem_total'] - gpu['mem_used']
        return gpu

    gpus = {}
    for num, lines in enumerate(gpu_lines):
        gpu = parse_gpu_lines(lines)
        assert num == gpu['num'], (
            'nums ({}, {}) do not agree. probably a parsing error'.format(num, gpu['num']))
        assert num not in gpus, (
            'Multiple GPUs labeled as num {}. Probably a parsing error'.format(num))
        gpus[num] = gpu
    return gpus


if __name__ == '__main__':
    r"""
    CommandLine:
        python -m netharn.device all
        pytest ~/code/netharn/netharn/device.py
    """
    import xdoctest
    xdoctest.doctest_module(__file__)
