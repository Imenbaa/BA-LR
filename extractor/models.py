
"""
models.py: This file contains the functions and class 
defining the ResNet and the classifier.
"""
# ==============================================================================
#  Copyright (c) 2022. Imen Ben Amor
# ==============================================================================

import math

import torch
import torch.nn as nn
import torch.nn.functional as F

from scipy.stats import kurtosis, skew


def conv3x3(in_planes, out_planes, stride=1, groups=1, dilation=1):
    """3x3 convolution with padding"""
    return nn.Conv2d(in_planes, out_planes, kernel_size=3, stride=stride,
                     padding=dilation, groups=groups, bias=False, dilation=dilation)

def conv1x1(in_planes, out_planes, stride=1):
    """1x1 convolution"""
    return nn.Conv2d(in_planes, out_planes, kernel_size=1, stride=stride, bias=False)

class BasicBlock(nn.Module):
    expansion = 1

    def __init__(self, inplanes, planes, stride=1, downsample=None, groups=1,
                 base_width=64, dilation=1, norm_layer=None):
        super(BasicBlock, self).__init__()

        if norm_layer is None:
            norm_layer = nn.BatchNorm2d
        if groups != 1 or base_width != 64:
            raise ValueError('BasicBlock only supports groups=1 and base_width=64')
        if dilation > 1:
            raise NotImplementedError("Dilation > 1 not supported in BasicBlock")
        
        # Both self.conv1 and self.downsample layers downsample the input when stride != 1
        self.conv1 = conv3x3(inplanes, planes, stride)
        self.bn1 = norm_layer(planes)
        self.relu = nn.ReLU(inplace=True)
        self.conv2 = conv3x3(planes, planes)
        self.bn2 = norm_layer(planes)
        self.downsample = downsample
        self.stride = stride

    def forward(self, x):
        identity = x

        out = self.conv1(x)
        out = self.bn1(out)
        out = self.relu(out)

        out = self.conv2(out)
        out = self.bn2(out)

        if self.downsample is not None:
            identity = self.downsample(x)

        out += identity
        out = self.relu(out)

        return out

class ResNet(nn.Module):

    def __init__(self, block, layers, num_filters, emb_size, pooling_mode='statistical', features_per_frame=30,
                 zero_init_residual=False, groups=1, width_per_group=64, replace_stride_with_dilation=None,
                 norm_layer=None):
        super(ResNet, self).__init__()
        if norm_layer is None:
            norm_layer = nn.BatchNorm2d
        self._norm_layer = norm_layer

        self.inplanes = num_filters[0]
        self.dilation = 1
        if replace_stride_with_dilation is None:
            # each element in the tuple indicates if we should replace
            # the 2x2 stride with a dilated convolution instead
            replace_stride_with_dilation = [False, False, False]
        if len(replace_stride_with_dilation) != 3:
            raise ValueError("replace_stride_with_dilation should be None "
                             "or a 3-element tuple, got {}".format(replace_stride_with_dilation))
        self.groups = groups
        self.base_width = width_per_group
        self.conv1 = nn.Conv2d(1, self.inplanes, kernel_size=3, stride=1, padding=1,
                               bias=False) # 3 1 1
        self.bn1 = norm_layer(self.inplanes)
        self.relu = nn.ReLU(inplace=True)

        self.layer1 = self._make_layer(block, num_filters[0], layers[0], stride=1)
        self.layer2 = self._make_layer(block, num_filters[1], layers[1], stride=2)
        self.layer3 = self._make_layer(block, num_filters[2], layers[2], stride=2)
        self.layer4 = self._make_layer(block, num_filters[3], layers[3], stride=2)
        
        self.pooling_mode = pooling_mode

        pooling_size = 2 if self.pooling_mode in ['statistical', 'std_skew', 'std_kurtosis']  else 1
        self.fc = nn.Linear(num_filters[3] * math.ceil(features_per_frame * (0.5 ** (len(layers) - 1))) * pooling_size, emb_size)
        self.softplus= nn.Softplus()


        for m in self.modules():
            if isinstance(m, nn.Conv2d):
                nn.init.kaiming_normal_(m.weight, mode='fan_out', nonlinearity='relu')
            elif isinstance(m, (nn.BatchNorm2d, nn.GroupNorm)):
                nn.init.constant_(m.weight, 1)
                nn.init.constant_(m.bias, 0)

        # Zero-initialize the last BN in each residual branch,
        # so that the residual branch starts with zeros, and each residual block behaves like an identity.
        # This improves the model by 0.2~0.3% according to https://arxiv.org/abs/1706.02677
        if zero_init_residual:
            for m in self.modules():
                if isinstance(m, BasicBlock):
                    nn.init.constant_(m.bn2.weight, 0)

    def _make_layer(self, block, planes, blocks, stride=1, dilate=False):
        norm_layer = self._norm_layer
        downsample = None
        previous_dilation = self.dilation
        if dilate:
            self.dilation *= stride
            stride = 1
        if stride != 1 or self.inplanes != planes * block.expansion:
            downsample = nn.Sequential(
                conv1x1(self.inplanes, planes * block.expansion, stride),
                norm_layer(planes * block.expansion),
            )

        layers = []
        layers.append(block(self.inplanes, planes, stride, downsample, self.groups,
                            self.base_width, previous_dilation, norm_layer))
        self.inplanes = planes * block.expansion
        for _ in range(1, blocks):
            layers.append(block(self.inplanes, planes, groups=self.groups,
                                base_width=self.base_width, dilation=self.dilation,
                                norm_layer=norm_layer))

        return nn.Sequential(*layers)

    def _forward_impl(self, x):
        x = self.conv1(x)
        x = self.bn1(x)
        x = self.relu(x)
        
        x = self.layer1(x)
        x = self.layer2(x)
        x = self.layer3(x)
        x = self.layer4(x)

        x = x.transpose(2, 3)
        x = x.flatten(1, 2)
        x = pooling(x, self.pooling_mode)

        x = self.fc(x)
        x = self.softplus(x)

        return x

    def forward(self, x):
        return self._forward_impl(x)


def resnet34(args, **kwargs):
    """ 
        A small funtion that initialize a resnet 34.
        Usage: 
            model = resnet34()
    """
    model = ResNet(BasicBlock,
                   args.layers,
                   args.num_filters,
                   args.emb_size, 
                   args.pooling,
                   args.features_per_frame,
                   args.zero_init_residual,
                   **kwargs)
    return model


def pooling(x, mode='statistical'):
    """
        function that implement different kind of pooling
    """
    if mode == 'min':
        x, _ = x.min(dim=2)
    elif mode == 'max':
        x, _ = x.min(dim=2)
    elif mode == 'mean':
        x = x.mean(dim=2)
    elif mode == 'std':
        x = x.std(dim=2)
    elif mode == 'statistical':
        means = x.mean(dim=2)
        stds = x.std(dim=2)
        x = torch.cat([means, stds], dim=1)
    elif mode == 'std_kurtosis':
        stds = x.std(dim=2)
        kurtoses = kurtosis(x.detach().cpu(), axis=2, fisher=False)
        kurtoses = torch.from_numpy(kurtoses)
        kurtoses = kurtoses.to(stds.device)
        x = torch.cat([stds, kurtoses], dim=1)
    elif mode == 'std_skew':
        stds = x.std(dim=2)
        skews = skew(x.detach().cpu(), axis=2)
        skews = torch.from_numpy(skews)
        skews = skews.to(stds.device)
        x = torch.cat([stds, skews], dim=1)
    else:
        raise ValueError('Unexpected pooling mode.')

    return x


class NeuralNetAMSM(nn.Module):
    """ 
        The classifier Neural Network
        AMSM stands for : Arc Margin SoftMax 
    """
    def __init__(self, num_features, num_classes, s=30.0, m=0.4):
        super(NeuralNetAMSM, self).__init__()
        self.num_features = num_features
        self.n_classes = num_classes
        self.s = s
        self.m = m
        self.W = nn.Parameter(torch.FloatTensor(num_classes, num_features))
        self.reset_parameters()

    def reset_parameters(self):
        nn.init.xavier_uniform_(self.W)

    def forward(self, input, label=None):
        # normalize features
        x = F.normalize(input)
        
        # normalize weights
        W = F.normalize(self.W)

        # dot product
        logits = F.linear(x, W)
        if label is None:
            return logits

        # add margin
        target_logits = logits - self.m
        one_hot = torch.zeros_like(logits)
        one_hot.scatter_(1, label.view(-1, 1).long(), 1)
        output = logits * (1 - one_hot) + target_logits * one_hot

        # feature re-scale
        output *= self.s

        return output
