import torch
import torch.nn as nn
from torch.autograd import Variable


class GRU(nn.Module):

    def __init__(self, input_size, hidden_size, output_size, num_layers=1,
                 dropout_hidden=.5, dropout_input=0, batch_size=50,
                 use_cuda=True, training=True):
        '''
        The GRU layer used for the whole GRU4REC model.

        Args:
            input_size (int): input layer dimension
            hidden_size (int): hidden layer dimension
            output_size (int): output layer dimension. Equivalent to the number of classes
            num_layers (int): the number of GRU layers
            dropout_hidden (float): dropout probability for the GRU hidden layers
            dropout_input (float): dropout probability for the GRU input layer
            batch_size (int): size of the training batch.(required for producing one-hot encodings efficiently)
            use_cuda (bool): whether to use cuda or not
            training (bool): whether to set the GRU module to training mode or not. If false, parameters will not be updated.
        '''

        super().__init__()
        self.input_size = input_size
        self.hidden_size = hidden_size
        self.output_size = output_size
        self.num_layers = num_layers
        self.dropout_input = dropout_input
        self.dropout_hidden = dropout_hidden

        self.batch_size = batch_size
        self.use_cuda = use_cuda
        self.training = training

        self.onehot_buffer = self.init_emb()  # the buffer where the one-hot encodings will be produced from
        self.h2o = nn.Linear(hidden_size, output_size)
        self.tanh = nn.Tanh()
        self.gru = nn.GRU(input_size, hidden_size, num_layers, dropout=dropout_hidden)

        if self.use_cuda:
            self = self.cuda()

    def forward(self, embedded, target, hidden):
        '''
        Args:
            embedded (B,C): a batch of embedded item indices from a session-parallel mini-batch.
            target (B,): torch.LongTensor of next item indices from a session-parallel mini-batch.
            
        Returns:
            (if self.mode == 'train')
            logit (B,B): Variable that stores the sampled logits for the next items in the session-parallel mini-batch
            (if self.mode == 'test')
            logit (B,C): Variable that stores the logits for the next items in the session-parallel mini-batch
        '''
        if self.training:
            # Apply dropout to inputs when training
            p_drop = torch.Tensor(embedded.size(0), 1).fill_(1 - self.dropout_input)  # (B,1)
            mask = Variable(torch.bernoulli(p_drop).expand_as(embedded))  # (B,C)
            if self.use_cuda: mask = mask.cuda()
            embedded = embedded * mask  # (B,C)
        embedded = embedded.unsqueeze(0)  # (1,B,C)

        # Go through the GRU layer
        output, hidden = self.gru(embedded, hidden)  # (num_layers,B,H)

        '''
        Sampling on the activation.
        Scores will be calculated on only the items appearing in this mini-batch.
        '''
        # self.h2o(output): (1,B,H)
        output = output.view(-1, output.size(-1))  # (B,H)
        logit = self.tanh(self.h2o(output))  # (B,C)

        if self.training:
            logit = logit[:, target.view(-1)]  # (B,B). Sample outputs

        return logit, hidden

    def emb(self, input, volatile=False):
        '''
        Returns a one-hot vector corresponding to the input
        
        Args:
            input (B,): torch.LongTensor of item indices
            
        Returns:
            one_hot (B,C): torch.FloatTensor of one-hot vectors
        '''
        # flush the buffer
        self.onehot_buffer.zero_()
        # fill the buffer with 1 where needed
        index = input.view(-1, 1)
        self.onehot_buffer.scatter_(1, index, 1)

        one_hot = Variable(self.onehot_buffer, volatile=volatile)

        return one_hot.cuda() if self.use_cuda else one_hot

    def init_emb(self):
        '''
        Initialize the one_hot embedding buffer, which will be used for producing the one-hot embeddings efficiently
        '''
        onehot_buffer = torch.FloatTensor(self.batch_size, self.output_size)
        if self.use_cuda: onehot_buffer = onehot_buffer.cuda()

        return onehot_buffer

    def init_hidden(self):
        '''
        Initialize the hidden state of the GRU
        '''
        h0 = Variable(torch.zeros(self.num_layers, self.batch_size, self.hidden_size))

        return h0.cuda() if self.use_cuda else h0

    def switch_mode(self):
        '''
        Switch the mode from Training/Test to Test/Training
        '''
        # update the current mode
        self.training = not self.training

        # print out the current mode
        mode = 'Training' if self.training else 'Testing'
        print(f'Switching into {mode} mode.')

        # turn on/off gradient updates
        for param in self.parameters():
            param.requires_grad = self.training

        # turn on/off dropouts on hidden layers
        self.gru.train(self.training)
