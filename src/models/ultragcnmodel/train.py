#!/usr/bin/env python
# -*- coding: utf-8 -*-

import torch
from models.ultragcnmodel.model import UltraGCN
from tqdm import tqdm
from utils import log_param
from loguru import logger
import time
import numpy as np

from models.ultragcnmodel.eval import test
from torch.utils.tensorboard import SummaryWriter


class UltraGCNTrainer:
    def __init__(self, device):
        self.device = device
        self.best_epoch, self.best_recall, self.best_ndcg = 0, 0, 0
        self.early_stop_count = 0
        self.early_stop = False

    def train_with_hyper_param(self, 
                               train_data, 
                               hyper_param,
                               ii_constraint_mat,
                               ii_neighbor_mat, 
                               verbose=False):

        batch_size = hyper_param['batch_size']
        epochs = hyper_param['epochs']
        learning_rate = hyper_param['learning_rate']

        train_loader = torch.utils.data.DataLoader(train_data,
                                                   batch_size=batch_size,
                                                   shuffle=True,
                                                   num_workers=5)
        
        batches = len(train_loader.dataset) // hyper_param['batch_size']
        if len(train_loader.dataset) % hyper_param['batch_size'] != 0:
            batches += 1
        print('Total training batches = {}'.format(batches))

        if hyper_param['enable_tensorboard']:
            writer = SummaryWriter()
        
        ultragcn = UltraGCN(hyper_param, ii_constraint_mat, ii_neighbor_mat).to(self.device)
        optimizer = torch.optim.Adam(ultragcn.parameters(), lr=learning_rate)

        pbar = tqdm(range(hyper_param['epochs']), leave=False, colour='green', desc='epoch')

        # for epoch in pbar:
        for epoch in range(hyper_param['max_epoch']):
            avg_loss = 0
            ultragcn.train()
            start_time = time.time()

            # x: tensor:[users, pos_items]
            # for batch, x in tqdm(train_loader, leave=False, colour='red', desc='batch'):
            for batch, x in enumerate(train_loader):
                users, pos_items, neg_items = self.Sampling(x,
                                                            hyper_param['item_num'],
                                                            hyper_param['negative_num'],
                                                            hyper_param['interacted_items'],
                                                            hyper_param['sampling_sift_pos'])
                users = users.to(self.device)
                pos_items = pos_items.to(self.device)
                neg_items = neg_items.to(self.device)

                ultragcn.zero_grad()
                loss = ultragcn(users, pos_items, neg_items)
                if hyper_param['enable_tensorboard']:
                    writer.add_scalar('Loss/train', loss, epoch * batches + batch)
                loss.backward()
                optimizer.step()

                avg_loss += loss / batches

            train_time = time.strftime('%H:%M:%S', time.gmtime(time.time() - start_time))
            if hyper_param['enable_tensorboard']:
                writer.add_scalar('Loss/train', loss, epoch)

            if verbose:
                pbar.write('Epoch {:02}: {:.4} training loss'.format(epoch, loss.item()))

            need_test = True
            if epoch < 50 and epoch % 5 != 0:
                need_test = False

            
            if need_test:
                start_time = time.time()
                test_loader = torch.utils.data.DataLoader(list(range(hyper_param['user_num'])),
                                                          batch_size=hyper_param['test_batch_size'],
                                                          shuffle=False,
                                                          num_workers=5)
                F1_score, Precision, Recall, NDCG = test(ultragcn,
                                                         test_loader,
                                                         hyper_param['test_ground_truth_list'],
                                                         hyper_param['mask'],
                                                         hyper_param['topk'],
                                                         hyper_param['user_num'])
                if hyper_param['enable_tensorboard']:
                    writer.add_scalar('Results/recall@20', Recall, epoch)
                    writer.add_scalar('Results/ndcg@20', NDCG, epoch)
                test_time = time.strftime('%H:%M:%S', time.gmtime(time.time() - start_time))

                print('The time for epoch {} is: train time = {}, test time = {}'.format(epoch, train_time, test_time))
                print("Loss = {:.5f}, F1-score: {:.5f} \t Precision: {:.5f}\t Recall: {:.5f}\tNDCG: {:.5f}".format(loss.item(), F1_score, Precision, Recall, NDCG))

                if Recall > self.best_recall:
                    self.best_recall, self.best_ndcg, self.best_epoch = Recall, NDCG, epoch
                    self.early_stop_count = 0
                    torch.save(ultragcn.state_dict(), hyper_param['model_save_path'])

                else:
                    self.early_stop_count += 1
                    if self.early_stop_count >= hyper_param['early_stop_epoch']:
                        self.early_stop = True
            
            if self.early_stop:
                print('##########################################')
                print('Early stop is triggered at {} epochs.'.format(epoch))
                print('Results:')
                print('best epoch = {}, best recall = {}, best ndcg = {}'.format(self.best_epoch, 
                                                                                 self.best_recall, 
                                                                                 self.best_ndcg))
                print('The best model is saved at {}'.format(hyper_param['model_save_path']))
                break

        writer.flush()
        pbar.close()

        print('Training end!')

        return ultragcn

    def Sampling(self,
                 pos_train_data, 
                 item_num, 
                 neg_ratio, 
                 interacted_items,
                 sampling_sift_pos):
        neg_candidates = np.arange(item_num)

        if sampling_sift_pos:
            neg_items = []
            for u in pos_train_data[0]:
                probs = np.ones(item_num)
                probs[interacted_items[u]] = 0
                probs /= np.sum(probs)

                u_neg_items = np.random.choice(neg_candidates,
                                               size=neg_ratio,
                                               p = probs,
                                               replace=True).reshape(1, -1)
                
                neg_items.append(u_neg_items)

            neg_items = np.concatenate(neg_items, axis=0)
        else:
            neg_items = np.random.choice(neg_candidates,
                                         (len(pos_train_data[0]), neg_ratio),
                                         replace=True)
            
        neg_items = torch.from_numpy(neg_items)

        # users, pos_items, neg_items
        return pos_train_data[0], pos_train_data[1], neg_items