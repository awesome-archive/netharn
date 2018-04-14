"""
This module can be used as both a script and an importable module.
Run `python train_ibeis_siam.py --help` for more details.
See docstring in fit for more details on the importable module.


conda install opencv
conda install pytorch torchvision -c pytorch

TestMe:
    xdoctest ~/code/netharn/netharn/examples/siam_ibeis.py all
"""
import ubelt as ub
import numpy as np
import netharn as nh
import torch
import torchvision
import itertools as it

__all__ = [
    'RandomBalancedIBEISSample',
    'SiameseLP',
    'SiamHarness',
    'randomized_ibeis_dset',
    'setup_harness',
    'fit',
    'main',

]


class RandomBalancedIBEISSample(torch.utils.data.Dataset):
    """
    Construct a pairwise image training dataset.

    CommandLine:
        xdoctest ~/code/netharn/netharn/examples/siam_ibeis.py RandomBalancedIBEISSample --show

    Example:
        >>> self = RandomBalancedIBEISSample.from_dbname('PZ_MTEST')
        >>> # xdoctest +REQUIRES(--show)
        >>> self.show_sample()
        >>> nh.util.show_if_requested()
    """
    SEED = 563401

    def __init__(self, pblm, pccs, dim=224, augment=True):
        chip_config = {
            # preserve aspect ratio, use letterbox to fit into network
            'resize_dim': 'maxwh',
            'dim_size': dim,

            # 'resize_dim': 'wh',
            # 'dim_size': (dim, dim)
        }
        self.pccs = pccs
        all_aids = list(ub.flatten(pccs))
        all_fpaths = pblm.infr.ibs.depc_annot.get(
            'chips', all_aids, read_extern=False, colnames='img',
            config=chip_config)

        self.aid_to_fpath = dict(zip(all_aids, all_fpaths))

        # self.multitons_pccs = [pcc for pcc in pccs if len(pcc) > 1]
        self.pos_pairs = []

        # SAMPLE ALL POSSIBLE POS COMBINATIONS AND IGNORE INCOMPARABLE
        self.infr = pblm.infr
        # TODO: each sample should be weighted depending on n_aids in its pcc
        for pcc in pccs:
            if len(pcc) >= 2:
                edges = np.array(list(it.starmap(self.infr.e_, it.combinations(pcc, 2))))
                is_comparable = self.is_comparable(edges)
                pos_edges = edges[is_comparable]
                self.pos_pairs.extend(list(pos_edges))
        rng = nh.util.ensure_rng(self.SEED, 'numpy')
        self.pyrng = nh.util.ensure_rng(self.SEED + 1, 'python')
        self.rng = rng

        # Be good data citizens, construct a dataset identifier
        depends = [
            sorted(map(sorted, self.pccs)),
        ]
        hashid = ub.hash_data(depends)[:12]
        self.input_id = '{}-{}'.format(len(self), hashid)

        if augment:
            import imgaug.augmenters as iaa
            self.augmenter = iaa.Sequential([
                nh.data.transforms.HSVShift(hue=0.1, sat=1.5, val=1.5),
                iaa.Crop(percent=(0, .2)),
                iaa.Fliplr(p=.5),
            ])
            self.hue = nh.data.transforms.HSVShift(hue=0.1, sat=1.5, val=1.5)
            self.crop = iaa.Crop(percent=(0, .2))
            self.flip = iaa.Fliplr(p=.5)
        else:
            self.augmenter = None
        self.letterbox = nh.data.transforms.Resize(target_size=(dim, dim), mode='letterbox')
        # self.colorspace = 'RGB'
        # self.center_inputs = None

    @classmethod
    def from_dbname(RandomBalancedIBEISSample, dbname='PZ_MTEST', dim=224):
        """
        dbname = 'PZ_MTEST'
        dim = 244
        """
        from ibeis.algo.verif import vsone
        pblm = vsone.OneVsOneProblem.from_empty(dbname)
        pccs = list(pblm.infr.positive_components())
        self = RandomBalancedIBEISSample(pblm, pccs, dim=dim)
        return self

    def __len__(self):
        return len(self.pos_pairs) * 2

    def show_sample(self):
        """
        CommandLine:
            python ~/code/netharn/netharn/examples/siam_ibeis.py RandomBalancedIBEISSample.show_sample --show

        Example:
            >>> self = RandomBalancedIBEISSample.from_dbname('PZ_MTEST')
            >>> ut.qtensure()
            >>> self.show_sample()
            >>> nh.util.show_if_requested()
        """
        vis_dataloader = torch.utils.data.DataLoader(self, shuffle=True,
                                                     batch_size=8)
        example_batch = next(iter(vis_dataloader))
        concatenated = torch.cat((example_batch[0], example_batch[1]), 0)
        tensor = torchvision.utils.make_grid(concatenated)
        im = tensor.numpy().transpose(1, 2, 0)
        nh.util.imshow(im)
        # import matplotlib.pyplot as plt
        # plt.imshow(im)

    def class_weights(self):
        class_weights = torch.FloatTensor([1.0, 1.0])
        return class_weights

    def is_comparable(self, edges):
        from ibeis.algo.graph.state import POSTV, NEGTV, INCMP, UNREV  # NOQA
        infr = self.infr
        def _check(u, v):
            if infr.incomp_graph.has_edge(u, v):
                return False
            elif infr.pos_graph.has_edge(u, v):
                # Only override if the evidence says its positive
                # otherwise guess
                ed = infr.get_edge_data((u, v)).get('evidence_decision', UNREV)
                if ed == POSTV:
                    return True
                else:
                    return np.nan
            elif infr.neg_graph.has_edge(u, v):
                return True
            return np.nan
        flags = np.array([_check(*edge) for edge in edges])
        # hack guess if comparable based on viewpoint
        guess_flags = np.isnan(flags)
        need_edges = edges[guess_flags]
        need_flags = infr.ibeis_guess_if_comparable(need_edges)
        flags[guess_flags] = need_flags
        return np.array(flags, dtype=np.bool)

    def get_aidpair(self, index):
        if index % 2 == 0:
            # Get a positive pair if the index is even
            aid1, aid2 = self.pos_pairs[index // 2]
            label = 1
        else:
            # Get a random negative pair if the index is odd
            pcc1, pcc2 = self.pyrng.sample(self.pccs, k=2)
            while pcc1 is pcc2:
                pcc1, pcc2 = self.pyrng.sample(self.pccs, k=2)
            aid1 = self.pyrng.sample(pcc1, k=1)[0]
            aid2 = self.pyrng.sample(pcc2, k=1)[0]
            label = 0
        return aid1, aid2, label

    def load_from_edge(self, aid1, aid2):
        """
        Example:
            >>> self = RandomBalancedIBEISSample.from_dbname('PZ_MTEST')
            >>> img1, img2 = self.load_from_edge(1, 2)
            >>> # xdoctest +REQUIRES(--show)
            >>> self.show_sample()
            >>> nh.util.qtensure()  # xdoc: +SKIP
            >>> nh.util.imshow(img1, pnum=(1, 2, 1), fnum=1)
            >>> nh.util.imshow(img2, pnum=(1, 2, 2), fnum=1)
            >>> nh.util.show_if_requested()
        """
        fpath1 = self.aid_to_fpath[aid1]
        fpath2 = self.aid_to_fpath[aid2]

        img1 = nh.util.imread(fpath1)
        img2 = nh.util.imread(fpath2)
        assert img1 is not None and img2 is not None

        if self.augmenter is not None:
            if True:
                # Augment hue and crop independently
                img1 = self.hue.forward(img1, self.rng)
                img2 = self.hue.forward(img2, self.rng)
                img1 = self.crop.augment_image(img1)
                img2 = self.crop.augment_image(img2)

                # Do the same flip for both images
                flip_det = self.flip.to_deterministic()
                img1 = flip_det.augment_image(img1)
                img2 = flip_det.augment_image(img2)
            else:
                # FIXME
                seq_det = self.augmenter.to_deterministic()
                img1 = seq_det.augment_image(img1)
                img2 = seq_det.augment_image(img2)

        img1 = self.letterbox.forward(img1)
        img2 = self.letterbox.forward(img2)

        return img1, img2

    def __getitem__(self, index):
        """
        Example:
            >>> self = RandomBalancedIBEISSample.from_dbname('PZ_MTEST')
            >>> index = 0
            >>> img1, img2, label = self[index]
        """
        aid1, aid2, label = self.get_aidpair(index)
        img1, img2 = self.load_from_edge(aid1, aid2)
        if self.augmenter is not None:
            if self.rng.rand() > .5:
                img1, img2 = img2, img1
        img1 = torch.FloatTensor(img1.transpose(2, 0, 1))
        img2 = torch.FloatTensor(img2.transpose(2, 0, 1))
        return img1, img2, label


def randomized_ibeis_dset(dbname, dim=224):
    """
    CommandLine:
        xdoctest ~/code/netharn/netharn/examples/siam_ibeis.py randomized_ibeis_dset

    Example:
        >>> # SCRIPT
        >>> datasets = randomized_ibeis_dset('PZ_MTEST')
        >>> nh.util.qtensure()
        >>> self = datasets['train']
        >>> self.show_sample()
    """
    import math
    from ibeis.algo.verif import vsone
    pblm = vsone.OneVsOneProblem.from_empty(dbname)

    pccs = list(pblm.infr.positive_components())
    pcc_freq = list(map(len, pccs))
    freq_grouped = ub.group_items(pccs, pcc_freq)

    # Simpler very randomized sample strategy
    train_pccs = []
    vali_pccs = []
    test_pccs = []

    vali_frac = .1
    test_frac = .1

    for i, group in freq_grouped.items():
        group = nh.util.shuffle(group, rng=432232 + i)
        n_test = 0 if len(group) == 1 else math.ceil(len(group) * test_frac)
        test, learn = group[:n_test], group[n_test:]
        n_vali = 0 if len(group) == 1 else math.ceil(len(learn) * vali_frac)
        vali, train = group[:n_vali], group[-n_vali:]
        train_pccs.extend(train)
        test_pccs.extend(test)
        vali_pccs.extend(vali)

    test_dataset = RandomBalancedIBEISSample(pblm, test_pccs, dim=dim)
    train_dataset = RandomBalancedIBEISSample(pblm, train_pccs, dim=dim,
                                              augment=False)
    vali_dataset = RandomBalancedIBEISSample(pblm, vali_pccs, dim=dim,
                                             augment=False)

    datasets = {
        'train': train_dataset,
        'vali': vali_dataset,
        'test': test_dataset,
    }
    datasets.pop('test', None)  # dont test for now (speed consideration)
    return datasets


class SiameseLP(torch.nn.Module):
    """
    Siamese pairwise distance

    Example:
        >>> self = SiameseLP()
    """

    def __init__(self, p=2, branch=None, input_shape=(1, 3, 224, 224)):
        super(SiameseLP, self).__init__()
        if branch is None:
            self.branch = torchvision.models.resnet50(pretrained=True)
        else:
            self.branch = branch
        assert isinstance(self.branch, torchvision.models.ResNet)
        prepool_shape = self.resnet_prepool_output_shape(input_shape)
        # replace the last layer of resnet with a linear embedding to learn the
        # LP distance between pairs of images.
        # Also need to replace the pooling layer in case the input has a
        # different size.
        self.prepool_shape = prepool_shape
        pool_channels = prepool_shape[1]
        pool_kernel = prepool_shape[2:4]
        self.branch.avgpool = torch.nn.AvgPool2d(pool_kernel, stride=1)
        self.branch.fc = torch.nn.Linear(pool_channels, 500)

        self.pdist = torch.nn.PairwiseDistance(p=p)

    def resnet_prepool_output_shape(self, input_shape):
        """
        self = SiameseLP(input_shape=input_shape)
        input_shape = (1, 3, 224, 224)
        self.resnet_prepool_output_shape(input_shape)
        self = SiameseLP(input_shape=input_shape)
        input_shape = (1, 3, 416, 416)
        self.resnet_prepool_output_shape(input_shape)
        """
        # Figure out how big the output will be and redo the average pool layer
        # to account for it
        branch = self.branch
        shape = input_shape
        shape = nh.OutputShapeFor(branch.conv1)(shape)
        shape = nh.OutputShapeFor(branch.bn1)(shape)
        shape = nh.OutputShapeFor(branch.relu)(shape)
        shape = nh.OutputShapeFor(branch.maxpool)(shape)

        shape = nh.OutputShapeFor(branch.layer1)(shape)
        shape = nh.OutputShapeFor(branch.layer2)(shape)
        shape = nh.OutputShapeFor(branch.layer3)(shape)
        shape = nh.OutputShapeFor(branch.layer4)(shape)
        prepool_shape = shape
        return prepool_shape

    def forward(self, input1, input2):
        """
        Compute a resnet50 vector for each input and look at the LP-distance
        between the vectors.

        Example:
            >>> input1 = nh.XPU(None).variable(torch.rand(4, 3, 224, 224))
            >>> input2 = nh.XPU(None).variable(torch.rand(4, 3, 224, 224))
            >>> self = SiameseLP(input_shape=input2.shape[1:])
            >>> output = self(input1, input2)

        Ignore:
            >>> input1 = nh.XPU(None).variable(torch.rand(1, 3, 416, 416))
            >>> input2 = nh.XPU(None).variable(torch.rand(1, 3, 416, 416))
            >>> input_shape1 = input1.shape
            >>> self = SiameseLP(input_shape=input2.shape[1:])
            >>> self(input1, input2)
        """
        output1 = self.branch(input1)
        output2 = self.branch(input2)
        output = self.pdist(output1, output2)
        return output

    def output_shape_for(self, input_shape1, input_shape2):
        shape1 = nh.OutputShapeFor(self.branch)(input_shape1)
        shape2 = nh.OutputShapeFor(self.branch)(input_shape2)
        assert shape1 == shape2
        output_shape = (shape1[0], 1)
        return output_shape


class SiamHarness(nh.FitHarn):

    def prepare_batch(harn, raw_batch):
        """
        ensure batch is in a standardized structure
        """
        img1, img2, label = raw_batch
        inputs = harn.xpu.variables(img1, img2)
        label = harn.xpu.variable(label)
        batch = (inputs, label)
        return batch

    def run_batch(harn, batch):
        """
        Connect data -> network -> loss

        Args:
            batch: item returned by the loader
        """
        inputs, label = batch
        output = harn.model(*inputs)
        loss = harn.criterion(output, label).sum()
        return output, loss

    def on_batch(harn, batch, output, loss):
        """ custom callback """
        label = batch[-1]
        l2_dist_tensor = torch.squeeze(output.data.cpu())
        label_tensor = torch.squeeze(label.data.cpu())

        # Distance
        POS_LABEL = 1  # NOQA
        NEG_LABEL = 0  # NOQA
        is_pos = (label_tensor == POS_LABEL)

        pos_dists = l2_dist_tensor[is_pos]
        neg_dists = l2_dist_tensor[~is_pos]

        # Average positive / negative distances
        pos_dist = pos_dists.sum() / max(1, len(pos_dists))
        neg_dist = neg_dists.sum() / max(1, len(neg_dists))

        # accuracy
        margin = harn.hyper.criterion_params['margin']
        pred_pos_flags = (l2_dist_tensor <= margin).long()

        pred = pred_pos_flags

        n_correct = (pred == label_tensor).sum()
        fraction_correct = n_correct / len(label_tensor)

        metrics = {
            'accuracy': fraction_correct,
            'pos_dist': pos_dist,
            'neg_dist': neg_dist,
        }
        return metrics

    def on_epoch(harn):
        """ custom callback """
        pass


def setup_harness(dbname='PZ_MTEST'):
    """
    CommandLine:
        python ~/code/netharn/netharn/examples/siam_ibeis.py setup_harness

    Example:
        >>> dbname = 'PZ_MTEST'
        >>> harn = setup_harness(dbname)
        >>> harn.initialize()
    """
    # TODO: setup as python function args and move to argparse
    nice = ub.argval('--nice', default='untitled_siam_ibeis')
    batch_size = int(ub.argval('--batch_size', default=6))
    bstep = int(ub.argval('--bstep', 4))
    workers = int(ub.argval('--workers', default=0))
    decay = float(ub.argval('--decay', default=0.0005))
    lr = float(ub.argval('--lr', default=0.001))
    dim = int(ub.argval('--dim', default=416))
    dbname = ub.argval('--db', default=dbname)

    datasets = randomized_ibeis_dset(dbname, dim=dim)
    workdir = ub.ensuredir(ub.truepath('~/work/siam-ibeis2/' + dbname))

    for k, v in datasets.items():
        print('* len({}) = {}'.format(k, len(v)))

    loaders = {
        key:  torch.utils.data.DataLoader(
            dset, batch_size=batch_size, num_workers=workers,
            shuffle=(key == 'train'), pin_memory=True)
        for key, dset in datasets.items()
    }

    xpu = nh.XPU.from_argv()
    hyper = nh.HyperParams(**{
        'nice': nice,
        'workdir': workdir,
        'datasets': datasets,
        'loaders': loaders,

        'xpu': xpu,

        'model': (SiameseLP, {
            'p': 2,
            'input_shape': (1, 3, dim, dim),
        }),

        'criterion': (nh.criterions.ContrastiveLoss, {
            'margin': 4,
            'weight': None,
        }),

        'optimizer': (torch.optim.SGD, {
            'lr': lr / 10,
            'weight_decay': decay,
            'momentum': 0.9,
            'nesterov': True,
        }),

        'initializer': (nh.initializers.NoOp, {}),

        'scheduler': (nh.schedulers.ListedLR, {
            'points': {
                # dividing by batch size was one of those unpublished details
                0:  lr / 10,
                1:  lr,
                59: lr * 1.1,
                60: lr / 10,
                90: lr / 100,
            },
            'interpolate': True
        }),

        'monitor': (nh.Monitor, {
            'minimize': ['loss', 'pos_dist'],
            'maximize': ['accuracy', 'neg_dist'],
            'patience': 160,
            'max_epoch': 160,
        }),

        'augment': datasets['train'].augmenter,

        'dynamics': {
            # Controls how many batches to process before taking a step in the
            # gradient direction. Effectively simulates a batch_size that is
            # `bstep` times bigger.
            'batch_step': bstep,
        },

        'other': {
            'n_classes': 2,
        },
    })
    harn = SiamHarness(hyper=hyper)
    harn.config['prog_backend'] = 'progiter'
    harn.intervals['log_iter_train'] = 1
    harn.intervals['log_iter_test'] = None
    harn.intervals['log_iter_vali'] = None

    return harn


def fit():
    r"""
    CommandLine:
        python examples/siam_ibeis.py fit --db PZ_MTEST --workers=0 --dim=32

        python examples/siam_ibeis.py fit --db PZ_Master1
        python examples/siam_ibeis.py fit --db PZ_MTEST --dry
        python examples/siam_ibeis.py fit --db RotanTurtles
        python examples/siam_ibeis.py fit --db humpbacks_fb

    Script:
        >>> # SCRIPT
        >>> fit()
    """
    harn = setup_harness()
    harn.run()


def main():
    import argparse
    description = ub.codeblock(
        '''
        Train the IBEIS siamese matcher
        ''')
    parser = argparse.ArgumentParser(prog='python examples/train_ibeis_siam.py', description=description)
    args, unknown = parser.parse_known_args()
    ns = args.__dict__.copy()
    fit(**ns)


if __name__ == '__main__':
    """
    CommandLine:
        python ~/code/netharn/netharn/examples/train_ibeis_siam.py
    """
    main()
