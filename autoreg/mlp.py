"""
The multilayer perceptron implementation according to deeplearning.net
"""

from GPy.core import Model, Parameterized, Param

import numpy as np
import theano
from theano import tensor, shared

theano.config.exception_verbosity='high'

class Layer(Parameterized):
    
    def __init__(self, dim_up, dim_down, activation, regularization=None, reg_weight=0, W=None, b=None, name='layer'):
        super(Layer,self).__init__(name=name)
        
        self.dim_down = dim_down
        self.dim_up = dim_up
        self.layer_forward = None # the link to its lower layer
        self.layer_backward = None # the link to its upper layer
        self.activation = None if activation is None else activation.lower()
        self.regularization = regularization
        self.reg_weight = reg_weight
        
        if W is None:
            W = np.random.rand(dim_down,dim_up)*2-1
            W *= np.sqrt(6./(dim_up+dim_down))
        if b is None:
            b = np.zeros((dim_down,))
        self.W = Param('W', W)
        self.b = Param('b', b)
        self.link_parameters(self.W, self.b)
                
        self.W_theano = shared(self.W.values.astype(theano.config.floatX),name=name+'_W')
        self.W_grad_theano = shared(self.W.gradient.astype(theano.config.floatX),name=name+'_W_grad')
        self.b_theano = shared(self.b.values.astype(theano.config.floatX),name=name+'_b')
        self.b_grad_theano = shared(self.b.gradient.astype(theano.config.floatX),name=name+'_b_grad')
        
    def link_layers(self, layer_backward, layer_forward):
        self.layer_forward = layer_forward # the link to its lower layer
        self.layer_backward = layer_backward # the link to its upper layer

    def _prepare_grad(self):
        # Sets to zeros theano gradients
        self.W_grad_theano.set_value(np.zeros_like(self.W.gradient, dtype=theano.config.floatX))
        self.b_grad_theano.set_value(np.zeros_like(self.b.gradient, dtype=theano.config.floatX))
        if self.layer_forward is not None:
            self.layer_forward._prepare_grad()

    def _prepare(self):
        self.W_theano.set_value(self.W.values.astype(theano.config.floatX))
        self.b_theano.set_value(self.b.values.astype(theano.config.floatX))
        if self.layer_forward is not None:
            self.layer_forward._prepare()

        
    def _update_gradient(self):
        # Copies gradient from theano to python parameter
        self.W.gradient[:] = self.W_grad_theano.get_value()
        self.b.gradient[:] = self.b_grad_theano.get_value()
        if self.layer_forward is not None:
            self.layer_forward._update_gradient()
        
    def _build_hidden_layers(self, input, add_cost, Y, updates, external_grad=None):
        
        lin_output = tensor.dot(input, self.W_theano.T)+self.b_theano[None,:]
        if self.activation=='tanh':
            output = tensor.tanh(lin_output)
        elif self.activation=='softplus':
            output = tensor.nnet.softplus(lin_output)
        elif self.activation is None:
            output = lin_output
        else:
            raise 'Unsupported activation function!'
        
        if self.regularization == 'L1':
            add_cost = add_cost -self.reg_weight*tensor.abs(self.W_theano).sum()
        elif self.regularization == 'L2':
            add_cost = add_cost -self.reg_weight*(self.W_theano**2).sum()
        
        # Compute the cost function
        if self.layer_forward is None:
            if external_grad is None:
                cost = -((output-Y)**2).sum()/self.sigma2_theano[0]+add_cost
            else:
                cost = (external_grad*output).sum()
            Y_out = output
        else:
            cost, Y_out = self.layer_forward._build_hidden_layers(output, add_cost, Y, updates, external_grad=external_grad)
            
        # Update parameter gradients
        W_grad = tensor.grad(cost, self.W_theano)
        b_grad = tensor.grad(cost, self.b_theano)
        updates.extend([(self.W_grad_theano,self.W_grad_theano+W_grad), (self.b_grad_theano,self.b_grad_theano+b_grad)])
        
        return cost, Y_out
        
    def build_theano_functions(self, external_grad=False):

        Y = tensor.matrix('Y')
        X = tensor.matrix('X')
        exGrad = tensor.matrix('exGrad') if external_grad else None            
        updates = []
        
        cost, Y_out = self._build_hidden_layers(X, 0, Y, updates, external_grad=exGrad)
        
        self._predict = theano.function([X], Y_out, allow_input_downcast=True,name='predict')
        if external_grad:
            X_grad = tensor.grad(cost, X)
            self._comp_grad = theano.function([X, exGrad], X_grad, updates=updates, allow_input_downcast=True,name='comp_grad')
        else:
            self._comp_cost = theano.function([X,Y], cost, updates=updates, allow_input_downcast=True,name='comp_cost')

class MLP(Parameterized):
    """Multi-Layer Perceptron Class"""

    def __init__(self, nUnits, activation='tanh', X_center=None, regularization=None, reg_weight=0, positive_obs=False, name='mlp'):
        super(MLP, self).__init__(name=name)
        
        self.Y = None
        self.X = None
        self.nLayers = len(nUnits)-1
        self.nUnits = nUnits
        self.X_center = X_center

        # Layers order from input to output
        self.layers = [Layer(nUnits[i],nUnits[i+1], activation=activation if i!=self.nLayers-1 else ('softplus' if positive_obs else None), regularization=regularization, reg_weight=reg_weight, name='layer_'+str(i+1)) for i in xrange(self.nLayers)]
        for i in xrange(self.nLayers):
            self.layers[i].link_layers(self.layers[i-1] if i>0 else None, self.layers[i+1] if i<self.nLayers-1 else None)
        self.link_parameters(*self.layers)
        self.theano_init = False
        
    def update_gradient(self, X, dL):
        if not self.theano_init:
            self.layers[0].build_theano_functions(external_grad=True)
            self.theano_init = True
        X = X if self.X_center is None else X-self.X_center
        if len(X.shape)==1: X = X[None,:]; dL = dL[None,:]
        X_grad = self.layers[0]._comp_grad(X, dL) 
        self.layers[0]._update_gradient() # Copy W, b gradients from theano to python parameters
        return X_grad
    
    def prepare_grad(self):
        self.layers[0]._prepare_grad()
                
    def predict(self, X):
        X = X if self.X_center is None else X-self.X_center
        if not self.theano_init:
            self.layers[0].build_theano_functions(external_grad=True)
            self.theano_init = True
        
        if len(X.shape)==1: X = X[None,:]
        return self.layers[0]._predict(X)
    
    def parameters_changed(self):
        super(MLP,self).parameters_changed()
        self.layers[0]._prepare()
        
class MLP_model(Model):
    def __init__(self, X, Y, nUnits, activation='tanh', X_center=None, regularization=None, reg_weight=0, positive_obs=False, name='mlp'):
        super(MLP_model, self).__init__(name)
        self.X = X
        self.Y = Y
        self.mlp = MLP(nUnits, activation=activation, X_center=X_center, regularization=regularization, reg_weight=reg_weight, positive_obs=positive_obs, name=name)
        self.link_parameter(self.mlp)

    def parameters_changed(self):
        self.mlp.prepare_grad()
        X_pred = self.mlp.predict(self.Y)
        self.cost = np.sum((self.X - X_pred)**2)
        dL_dXpred = -(2*X_pred -2*self.X)
        self.mlp.update_gradient(self.Y, dL_dXpred)

    def log_likelihood(self):
        return -self.cost
