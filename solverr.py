# -*- coding: utf-8 -*-
"""
Created on Tue Aug 25 20:24:17 2020

@author: fatemeh nourian
"""

#my first attempt to train convTasNet - 99/06/04
# General config
# Task related
train_dir='D:/Amir Kabir University/thesis/conv-tasnet/Conv-TasNet-master/outdata/tr'
valid_dir='D:/Amir Kabir University/thesis/conv-tasnet/Conv-TasNet-master/outdata/cv'
sample_rate=16000
segment=4
cv_maxlen=8
# Network architecture
N=256
#Number of filters in autoencoder
L=20
#Length of the filters in samples (40=5ms at 8kHZ)
B=256
#Number of channels in bottleneck 1 × 1-conv block
H=512
#Number of channels in convolutional blocks
P=3
#Kernel size in convolutional blocks
X=8
#Number of convolutional blocks in each repeat
R=4
#Number of repeats
C=2
#Number of speakers
norm_type='gLN'
#Layer norm type:['gLN', 'cLN', 'BN']
causal=0
#Causal (1) or noncausal(0) training
mask_nonlinear='relu'
#non-linear to generate mask:['relu','softmax']
# Training config
use_cuda=0
#Whether use GPU, default=1
epochs=30
#Number of maximum epochs
half_lr=0
#Halving learning rate when get small improvement
early_stop=0
#Early stop training when no improvement for 10 epochs
max_norm=5
#Gradient norm threshold to clip
# minibatch
shuffle=0
#reshuffle the data at every epoch
batch_size=128
num_workers=4
#Number of workers to generate minibatch
# optimizer
optimizer='adam'
#['sgd', 'adam']
lr=1e-3
#Init learning rate
momentum=0.0
#Momentum for optimizer
l2=0.0
#weight decay (L2 penalty)
# save and load model
save_folder='exp/temp'
#Location to save epoch models
checkpoint=0
#Enables checkpoint saving of model
continue_from=''
#Continue from checkpoint model
model_path='final.pth.tar'
#Location to save best validation model
# logging
print_freq=10
#Frequency of printing training infomation
visdom=0
#Turn on visdom graphing
visdom_epoch=0
#Turn on visdom graphing each epoch
visdom_id='TasNet training'
#Identifier for visdom run


#####################

import os
import time

import torch

from pit_criterion import cal_loss


class Solver(object):
    
    def __init__(self, data, model, optimizer, use_cuda,epochs,half_lr,early_stop,max_norm,save_folder,checkpoint,continue_from,model_path,print_freq,visdom,visdom_epoch,visdom_id):
        self.tr_loader = data['tr_loader']
        self.cv_loader = data['cv_loader']
        self.model = model
        self.optimizer = optimizer

        # Training config
        self.use_cuda = use_cuda
        self.epochs = epochs
        self.half_lr = half_lr
        self.early_stop = early_stop
        self.max_norm = max_norm
        # save and load model
        self.save_folder = save_folder
        self.checkpoint = checkpoint
        self.continue_from = continue_from
        self.model_path = model_path
        # logging
        self.print_freq = print_freq
        # visualizing loss using visdom
        self.tr_loss = torch.Tensor(self.epochs)
        self.cv_loss = torch.Tensor(self.epochs)
        self.visdom = visdom
        self.visdom_epoch = visdom_epoch
        self.visdom_id = visdom_id
        if self.visdom:
            from visdom import Visdom
            self.vis = Visdom(env=self.visdom_id)
            self.vis_opts = dict(title=self.visdom_id,
                                 ylabel='Loss', xlabel='Epoch',
                                 legend=['train loss', 'cv loss'])
            self.vis_window = None
            self.vis_epochs = torch.arange(1, self.epochs + 1)

        self._reset()

    def _reset(self):
        # Reset
        if self.continue_from:
            print('Loading checkpoint model %s' % self.continue_from)
            package = torch.load(self.continue_from)
            self.model.module.load_state_dict(package['state_dict'])
            self.optimizer.load_state_dict(package['optim_dict'])
            self.start_epoch = int(package.get('epoch', 1))
            self.tr_loss[:self.start_epoch] = package['tr_loss'][:self.start_epoch]
            self.cv_loss[:self.start_epoch] = package['cv_loss'][:self.start_epoch]
        else:
            self.start_epoch = 0
        # Create save folder
        os.makedirs(self.save_folder, exist_ok=True)
        self.prev_val_loss = float("inf")
        self.best_val_loss = float("inf")
        self.halving = False
        self.val_no_impv = 0

    def train(self):
        # Train model multi-epoches
        for epoch in range(self.start_epoch, self.epochs):
            # Train one epoch
            print("Training...")
            self.model.train()  # Turn on BatchNorm & Dropout
            start = time.time()
            tr_avg_loss = self._run_one_epoch(epoch)
            print('-' * 85)
            print('Train Summary | End of Epoch {0} | Time {1:.2f}s | '
                  'Train Loss {2:.3f}'.format(
                      epoch + 1, time.time() - start, tr_avg_loss))
            print('-' * 85)

            # Save model each epoch
            if self.checkpoint:
                file_path = os.path.join(
                    self.save_folder, 'epoch%d.pth.tar' % (epoch + 1))
                torch.save(self.model.module.serialize(self.model.module,
                                                       self.optimizer, epoch + 1,
                                                       tr_loss=self.tr_loss,
                                                       cv_loss=self.cv_loss),
                           file_path)
                print('Saving checkpoint model to %s' % file_path)

            # Cross validation
            print('Cross validation...')
            self.model.eval()  # Turn off Batchnorm & Dropout
            val_loss = self._run_one_epoch(epoch, cross_valid=True)
            print('-' * 85)
            print('Valid Summary | End of Epoch {0} | Time {1:.2f}s | '
                  'Valid Loss {2:.3f}'.format(
                      epoch + 1, time.time() - start, val_loss))
            print('-' * 85)

            # Adjust learning rate (halving)
            if self.half_lr:
                if val_loss >= self.prev_val_loss:
                    self.val_no_impv += 1
                    if self.val_no_impv >= 3:
                        self.halving = True
                    if self.val_no_impv >= 10 and self.early_stop:
                        print("No imporvement for 10 epochs, early stopping.")
                        break
                else:
                    self.val_no_impv = 0
            if self.halving:
                optim_state = self.optimizer.state_dict()
                optim_state['param_groups'][0]['lr'] = \
                    optim_state['param_groups'][0]['lr'] / 2.0
                self.optimizer.load_state_dict(optim_state)
                print('Learning rate adjusted to: {lr:.6f}'.format(
                    lr=optim_state['param_groups'][0]['lr']))
                self.halving = False
            self.prev_val_loss = val_loss

            # Save the best model
            self.tr_loss[epoch] = tr_avg_loss
            self.cv_loss[epoch] = val_loss
            if val_loss < self.best_val_loss:
                self.best_val_loss = val_loss
                file_path = os.path.join(self.save_folder, self.model_path)
                torch.save(self.model.module.serialize(self.model.module,
                                                       self.optimizer, epoch + 1,
                                                       tr_loss=self.tr_loss,
                                                       cv_loss=self.cv_loss),
                           file_path)
                print("Find better validated model, saving to %s" % file_path)

            # visualizing loss using visdom
            if self.visdom:
                x_axis = self.vis_epochs[0:epoch + 1]
                y_axis = torch.stack(
                    (self.tr_loss[0:epoch + 1], self.cv_loss[0:epoch + 1]), dim=1)
                if self.vis_window is None:
                    self.vis_window = self.vis.line(
                        X=x_axis,
                        Y=y_axis,
                        opts=self.vis_opts,
                    )
                else:
                    self.vis.line(
                        X=x_axis.unsqueeze(0).expand(y_axis.size(
                            1), x_axis.size(0)).transpose(0, 1),  # Visdom fix
                        Y=y_axis,
                        win=self.vis_window,
                        update='replace',
                    )

    def _run_one_epoch(self, epoch, cross_valid=False):
        start = time.time()
        total_loss = 0

        data_loader = self.tr_loader if not cross_valid else self.cv_loader

        # visualizing loss using visdom
        if self.visdom_epoch and not cross_valid:
            vis_opts_epoch = dict(title=self.visdom_id + " epoch " + str(epoch),
                                  ylabel='Loss', xlabel='Epoch')
            vis_window_epoch = None
            vis_iters = torch.arange(1, len(data_loader) + 1)
            vis_iters_loss = torch.Tensor(len(data_loader))

        for i, (data) in enumerate(data_loader):
            padded_mixture, mixture_lengths, padded_source = data
            if self.use_cuda:
                padded_mixture = padded_mixture.cuda()
                mixture_lengths = mixture_lengths.cuda()
                padded_source = padded_source.cuda()
            estimate_source = self.model(padded_mixture)
            loss, max_snr, estimate_source, reorder_estimate_source = \
                cal_loss(padded_source, estimate_source, mixture_lengths)
            if not cross_valid:
                self.optimizer.zero_grad()
                loss.backward()
                torch.nn.utils.clip_grad_norm_(self.model.parameters(),
                                               self.max_norm)
                self.optimizer.step()

            total_loss += loss.item()

            if i % self.print_freq == 0:
                print('Epoch {0} | Iter {1} | Average Loss {2:.3f} | '
                      'Current Loss {3:.6f} | {4:.1f} ms/batch'.format(
                          epoch + 1, i + 1, total_loss / (i + 1),
                          loss.item(), 1000 * (time.time() - start) / (i + 1)),
                      flush=True)

            # visualizing loss using visdom
            if self.visdom_epoch and not cross_valid:
                vis_iters_loss[i] = loss.item()
                if i % self.print_freq == 0:
                    x_axis = vis_iters[:i+1]
                    y_axis = vis_iters_loss[:i+1]
                    if vis_window_epoch is None:
                        vis_window_epoch = self.vis.line(X=x_axis, Y=y_axis,
                                                         opts=vis_opts_epoch)
                    else:
                        self.vis.line(X=x_axis, Y=y_axis, win=vis_window_epoch,
                                      update='replace')

        return total_loss / (i + 1)

