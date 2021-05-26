import json
import pickle
import torch
from torch import optim 
from torch.utils.data import DataLoader 
from tqdm import tqdm  # For nice progress bar!
import os
import numpy as np
import matplotlib.pyplot as plt
from cmn.team import Team
import mdl.param
from dal.data_utils import *
from mdl.fnn import FNN
from mdl.custom_dataset import TFDataset
from torch.optim.lr_scheduler import StepLR, ReduceLROnPlateau
import csv
import ast


def weighted_cross_entropy_with_logits(logits, targets, pos_weight = 2.5):
    return -targets * torch.log(torch.sigmoid(logits)) * pos_weight + (1 - targets) * -torch.log(1 - torch.sigmoid(logits))


# Set device cuda for GPU if it's available otherwise run on the CPU
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

num_nodes = mdl.param.fnn['d']
learning_rate = mdl.param.fnn['lr']
batch_size = mdl.param.fnn['b']
num_epochs = mdl.param.fnn['e']

def learn(teams, skill_to_index, member_to_index, index_to_skill, index_to_member, splits):
    input_matrix, output_matrix = Team.build_dataset_fnn(teams, skill_to_index, member_to_index)
    
    # Retrieving some of the required information for the model
    number_of_teams = len(teams)
    input_size = len(index_to_skill)
    output_size = len(index_to_member)
    
    # Specify a path for the model
    PATH = f"../output/fnn/nt{number_of_teams}_is{input_size}_os{output_size}_nn{num_nodes}_lr{learning_rate}_bs{batch_size}_ne{num_epochs}"
    if not os.path.isdir(PATH): os.mkdir(PATH)
    else:
        print("This model already exists")
        return input_matrix, output_matrix, PATH


    # Prime a dict for train and valid loss
    train_valid_loss = dict()
    for i in range(len(splits['folds'].keys())):
        train_valid_loss[i] = {'train' : [], 'valid' : []}

    # Training K-fold
    for foldidx in splits['folds'].keys():
        # Retrieving the folds
        X_train = input_matrix[splits['folds'][foldidx]['train'], :]
        y_train = output_matrix[splits['folds'][foldidx]['train']]
        X_valid = input_matrix[splits['folds'][foldidx]['valid'], :]
        y_valid = output_matrix[splits['folds'][foldidx]['valid']]

        # Building custom dataset
        training_matrix = TFDataset(X_train, y_train)
        validation_matrix = TFDataset(X_valid, y_valid)

        # Generating dataloaders
        training_dataloader = DataLoader(training_matrix, batch_size=batch_size, shuffle=True, num_workers=0)
        validation_dataloader = DataLoader(validation_matrix, batch_size=batch_size, shuffle=True, num_workers=0)

        data_loaders = {"train": training_dataloader, "val": validation_dataloader}

        # Initialize network
        model = FNN(input_size=input_size, output_size=output_size, param=mdl.param.fnn).to(device)


        # Loss and optimizer

        # criterion = nn.MSELoss()
        # criterion = nn.MultiLabelMarginLoss()
        # criterion = nn.BCELoss()
        # criterion = nn.BCEWithLogitsLoss()
        # criterion = nn.MultiLabelSoftMarginLoss()

        optimizer = optim.Adam(model.parameters(), lr=learning_rate)
        scheduler = ReduceLROnPlateau(optimizer, factor = 0.5, patience = 3, verbose = True)
        # scheduler = StepLR(optimizer, step_size=3, gamma=0.9)


        train_loss_values = []
        valid_loss_values = []

        # Train Network
        for epoch in range(num_epochs):
            print('Epoch {}/{}'.format(epoch+1, num_epochs))
            print('-' * 15)

            train_running_loss = 0.0    
            valid_running_loss = 0.0

            # Each epoch has a training and validation phase
            for phase in ['train', 'val']:
                if phase == 'train':
                    model.train(True)  # Set model to training mode
                    # scheduler.step()
                else:
                    model.train(False)  # Set model to evaluate mode

                # Iterating through mini-batches    
                for batch_idx, (data, targets) in tqdm(enumerate(data_loaders[phase])):
                    # Get data to cuda if possible
                    data = data.float().to(device=device)
                    targets = targets.float().to(device=device)

                    # forward
                    scores = model(data)

                    loss = weighted_cross_entropy_with_logits(scores, targets)
                    print(loss.sum().item())
                    
                    # Summing the loss of mini-batches
                    if phase == 'train':
                        train_running_loss += loss.sum().item()
                    else:
                        valid_running_loss += loss.sum().item()
                        
                    # backward
                    optimizer.zero_grad()
                    if phase == 'train': 
                        loss.sum().backward()
                        optimizer.step()

                # Appending the loss of each epoch to plot later
                if phase == 'train':
                    train_loss_values.append((train_running_loss/X_train.shape[0]))
                else:
                    valid_loss_values.append((valid_running_loss/X_valid.shape[0]))

            scheduler.step(valid_running_loss/X_valid.shape[0])

         
        model_path = f"{PATH}/state_dict_model_{foldidx}.pt"
    
        # Save
        torch.save(model.state_dict(), model_path)
        train_valid_loss[foldidx]['train'] = train_loss_values
        train_valid_loss[foldidx]['valid'] = valid_loss_values

    plot_path = f"{PATH}/train_valid_loss.json"
    
    with open(plot_path, 'w') as outfile:
        json.dump(train_valid_loss, outfile)

    return input_matrix, output_matrix, PATH

def plot(plot_path):
    with open(plot_path) as infile:
        train_valid_loss = json.load(infile)
    for foldidx in train_valid_loss.keys():
        plt.plot(train_valid_loss[foldidx]['train'], label='Training Loss')
        plt.plot(train_valid_loss[foldidx]['valid'], label='Validation Loss')
        plt.legend(loc='upper right')
        plt.title(f'Training and Validation Loss for fold #{foldidx}')
        plt.show()  

def eval(PATH, splits, input_matrix, output_matrix, index_to_skill, index_to_member):
    if not os.path.isdir(PATH):
        print("The model does not exist!")
        return
    input_size = len(index_to_skill)
    output_size = len(index_to_member)    
    
    auc_path = f"{PATH}/train_valid_auc.json"
    if os.path.isfile(auc_path):
        print(f"Reports can be found in: {PATH}")
        return

    # Initialize auc dictionary
    auc = dict()
    for foldidx in splits['folds'].keys():
        auc[foldidx] = {'train' : "", 'valid' : ""} 


    # Load
    for foldidx in splits['folds'].keys():
        X_train = input_matrix[splits['folds'][foldidx]['train'], :]
        y_train = output_matrix[splits['folds'][foldidx]['train']]
        X_valid = input_matrix[splits['folds'][foldidx]['valid'], :]
        y_valid = output_matrix[splits['folds'][foldidx]['valid']]

        training_matrix = TFDataset(X_train, y_train)
        validation_matrix = TFDataset(X_valid, y_valid)

        training_dataloader = DataLoader(training_matrix, batch_size=batch_size, shuffle=True, num_workers=0)
        validation_dataloader = DataLoader(validation_matrix, batch_size=batch_size, shuffle=True, num_workers=0)

        model_path = f'{PATH}/state_dict_model_{foldidx}.pt'
        model = FNN(input_size=input_size, output_size=output_size, param=mdl.param.fnn).to(device)
        model.load_state_dict(torch.load(model_path))
        model.eval()
        

        # Measure AUC for each fold and store in dict to later save as json
        auc_train = roc_auc(training_dataloader, model, device)
        auc_valid = roc_auc(validation_dataloader, model, device)
        auc[foldidx]['train'] = auc_train
        auc[foldidx]['valid'] = auc_valid

        # Measure Precision, recall, and F1 and save to txt
        train_rep_path = f'{PATH}/train_rep_{foldidx}.txt'
        if not os.path.isfile(train_rep_path):
            cls = cls_rep(training_dataloader, model, device)
            with open(train_rep_path, 'w') as outfile:
                outfile.write(cls)
        else:
            with open(train_rep_path, 'r') as infile:
                cls = infile.read()
        
        print(f"Training report of fold{foldidx}:\n", cls)

        valid_rep_path = f'{PATH}/valid_rep_{foldidx}.txt'
        if not os.path.isfile(valid_rep_path):
            cls = cls_rep(validation_dataloader, model, device)
            with open(valid_rep_path, 'w') as outfile:
                outfile.write(cls)
        else:
            with open(valid_rep_path, 'r') as infile:
                cls = infile.read()

        print(f"Validation report of fold{foldidx}:\n", cls)

    with open(auc_path, 'w') as outfile:
        json.dump(auc, outfile)

def test(PATH, splits, input_matrix, output_matrix, index_to_skill, index_to_member):
    if not os.path.isdir(PATH):
        print("The model does not exist!")
        return

    auc_path = f"{PATH}/test_auc.json"

    if os.path.isfile(auc_path):
        print(f"Reports can be found in: {PATH}")
        return

    input_size = len(index_to_skill)
    output_size = len(index_to_member)  

    # Load
    X_test = input_matrix[splits['test'], :]
    y_test = output_matrix[splits['test']]

    test_matrix = TFDataset(X_test, y_test)

    test_dataloader = DataLoader(test_matrix, batch_size=batch_size, shuffle=True, num_workers=0)
    
    # Initialize auc dictionary
    auc = dict()
    for foldidx in splits['folds'].keys():
        auc[foldidx] = ""
    
    for foldidx in splits['folds'].keys():
        model_path = f'{PATH}/state_dict_model_{foldidx}.pt'
        model = FNN(input_size=input_size, output_size=output_size, param=mdl.param.fnn).to(device)
        model.load_state_dict(torch.load(model_path))
        model.eval()

        # Measure AUC for each fold and store in dict to later save as json
        auc_test = roc_auc(test_dataloader, model, device)
        auc[foldidx] = auc_test

        # Measure Precision, recall, and F1 and save to txt
        test_rep_path = f'{PATH}/test_rep_{foldidx}.txt'
        if not os.path.isfile(test_rep_path):
            cls = cls_rep(test_dataloader, model, device)
            with open(test_rep_path, 'w') as outfile:
                outfile.write(cls)
        else:
            with open(test_rep_path, 'r') as infile:
                cls = infile.read()
        
        print(f"Test report of fold{foldidx}:\n", cls)

    with open(auc_path, 'w') as outfile:
        json.dump(auc, outfile)

def main(splits, teams, skill_to_index, member_to_index, index_to_skill, index_to_member, cmd=['plot', 'eval', 'test']):
    # if 'train' in cmd or 'eval' in cmd:
    input_matrix, output_matrix, PATH = learn(teams, skill_to_index, member_to_index, index_to_skill, index_to_member, splits)
   
    if 'plot' in cmd:
        plot_path = f"{PATH}/train_valid_loss.json"
        plot(plot_path)

    if 'eval' in cmd:
        eval(PATH, splits, input_matrix, output_matrix, index_to_skill, index_to_member)
        # eval_phase(PATH, splits, phases = ['train'])

    if 'test' in cmd:
        test(PATH, splits, input_matrix, output_matrix, index_to_skill, index_to_member)