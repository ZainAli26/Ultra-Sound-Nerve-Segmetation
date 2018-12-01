import sys
from keras.models import Model
from keras.layers import Input, merge, Convolution2D, MaxPooling2D, UpSampling2D, Dense, concatenate, add, Conv2D
from keras.layers import BatchNormalization, Dropout, Flatten, Lambda
from keras.layers.advanced_activations import ELU, LeakyReLU
from metric import dice_coef, dice_coef_loss

IMG_ROWS, IMG_COLS = 80, 112 

def _shortcut(_input, residual):
    stride_width = _input._keras_shape[2] / residual._keras_shape[2]
    stride_height = _input._keras_shape[3] / residual._keras_shape[3]
    equal_channels = residual._keras_shape[1] == _input._keras_shape[1]

    shortcut = _input
    # 1 X 1 conv if shape is different. Else identity.
    if stride_width > 1 or stride_height > 1 or not equal_channels:
        shortcut = Conv2D(nb_filter=residual._keras_shape[1], nb_row=1, nb_col=1,
                                 subsample=(stride_width, stride_height),
                                 init="he_normal", padding="valid")(_input)

    return add([shortcut, residual])


def inception_block(inputs, depth, batch_mode=0, splitted=False, activation='relu'):
    assert depth % 16 == 0
    actv = activation == 'relu' and (lambda: LeakyReLU(0.0)) or activation == 'elu' and (lambda: ELU(1.0)) or None
    c1_1 = Conv2D(int(depth/4), 1, strides=1, padding='same')(inputs)
    
    c2_1 = Conv2D(int(depth/8*3), 1, strides=1, padding='same')(inputs)
    c2_1 = actv()(c2_1)
    if splitted:
        c2_2 = Conv2D(int(depth/2), (1,3), strides=1, padding='same')(c2_1)	
        c2_2 = BatchNormalization(axis=3)(c2_2)
        c2_2 = actv()(c2_2)
        c2_3 = Conv2D(int(depth/2), (3,1), strides=1, padding='same')(c2_2)
    else:
        c2_3 = Conv2D(int(depth/2), 3, strides=1, padding='same')(c2_1)
    
    c3_1 = Conv2D(int(depth/16), 1, strides=1, padding='same')(inputs)
    #missed batch norm
    c3_1 = actv()(c3_1)
    if splitted:
        c3_2 = Conv2D(int(depth/8), (1,5), strides=1, padding='same')(c3_1)
        c3_2 = BatchNormalization(axis=3)(c3_2)
        c3_2 = actv()(c3_2)
        c3_3 = Conv2D(int(depth/8), (5,1), strides=1, padding='same')(c3_2)
    else:
        c3_3 = Conv2D(int(depth/8), 5, strides=1, padding='same')(c3_1)
    
    p4_1 = MaxPooling2D(pool_size=(3,3), strides=(1,1), padding='same')(inputs)
    c4_2 = Conv2D(int(depth/8), 1, strides=1, padding='same')(p4_1)
    
    res = concatenate([c1_1, c2_3, c3_3, c4_2], axis=3)
    res = BatchNormalization(axis=3)(res)
    res = actv()(res)
    print(res)
    return res
    

def rblock(inputs, num, depth, scale=0.1):    
    residual = Conv2D(depth, num, strides=1, padding='same')(inputs)
    residual = BatchNormalization(axis=3)(residual)
    residual = Lambda(lambda x: x*scale)(residual)
    res = _shortcut(inputs, residual)
    return ELU()(res) 
    

def NConvolution2D(nb_filter, nb_row, nb_col, padding='same', subsample=(1, 1)):
    def f(_input):
        conv = Conv2D(nb_filter, nb_row, strides=2,
                              padding=padding)(_input)
        norm = BatchNormalization(axis=3)(conv)
        return ELU()(norm)

    return f

def BNA(_input):
    inputs_norm = BatchNormalization(axis=3)(_input)
    return ELU()(inputs_norm)

def reduction_a(inputs, k=64, l=64, m=96, n=96):
    "35x35 -> 17x17"
    inputs_norm = BNA(inputs)
    pool1 = MaxPooling2D((3,3), strides=(2,2), padding='same')(inputs_norm)
    
    conv2 = Conv2D(n, 3, 3, subsample=(2,2), padding='same')(inputs_norm)
    
    conv3_1 = NConvolution2D(k, 1, 1, subsample=(1,1), padding='same')(inputs_norm)
    conv3_2 = NConvolution2D(l, 3, 3, subsample=(1,1), padding='same')(conv3_1)
    conv3_2 = Conv2D(m, 3, 3, subsample=(2,2), border_mode='same')(conv3_2)
    
    res = concatenate([pool1, conv2, conv3_2], axis=3)
    return res


def reduction_b(inputs):
    "17x17 -> 8x8"
    inputs_norm = BNA(inputs)
    pool1 = MaxPooling2D((3,3), strides=(2,2), border_mode='same')(inputs_norm)
    #
    conv2_1 = NConvolution2D(64, 1, 1, subsample=(1,1), border_mode='same')(inputs_norm)
    conv2_2 = Conv2D(96, 3, 3, subsample=(2,2), padding='same')(conv2_1)
    #
    conv3_1 = NConvolution2D(64, 1, 1, subsample=(1,1), border_mode='same')(inputs_norm)
    conv3_2 = Conv2D(72, 3, 3, subsample=(2,2), padding='same')(conv3_1)
    #
    conv4_1 = NConvolution2D(64, 1, 1, subsample=(1,1), border_mode='same')(inputs_norm)
    conv4_2 = NConvolution2D(72, 3, 3, subsample=(1,1), border_mode='same')(conv4_1)
    conv4_3 = Conv2D(80, 3, 3, subsample=(2,2), padding='same')(conv4_2)
    #
    res = concatenate([pool1, conv2_2, conv3_2, conv4_3], axis=3)
    return res
    
    


def get_unet_inception_2head(optimizer):
    splitted = True
    act = 'elu'
    
    inputs = Input((IMG_ROWS, IMG_COLS, 1), name='main_input')
    #print(inputs)
    conv1 = inception_block(inputs, 32, batch_mode=2, splitted=splitted, activation=act)
    #conv1 = inception_block(conv1, 32, batch_mode=2, splitted=splitted, activation=act)
    
    #pool1 = MaxPooling2D(pool_size=(2, 2))(conv1)
    pool1 = NConvolution2D(32, 3, 3, padding='same', subsample=(2,2))(conv1)
    pool1 = Dropout(0.5)(pool1)
    
    conv2 = inception_block(pool1, 64, batch_mode=2, splitted=splitted, activation=act)
    #pool2 = MaxPooling2D(pool_size=(2, 2))(conv2)
    pool2 = NConvolution2D(64, 3, 3, padding='same', subsample=(2,2))(conv2)
    pool2 = Dropout(0.5)(pool2)
    
    conv3 = inception_block(pool2, 128, batch_mode=2, splitted=splitted, activation=act)
    #pool3 = MaxPooling2D(pool_size=(2, 2))(conv3)
    pool3 = NConvolution2D(128, 3, 3, padding='same', subsample=(2,2))(conv3)
    pool3 = Dropout(0.5)(pool3)
     
    conv4 = inception_block(pool3, 256, batch_mode=2, splitted=splitted, activation=act)
    #pool4 = MaxPooling2D(pool_size=(2, 2))(conv4)
    print(conv4)
    pool4 = NConvolution2D(256, 3, 3, padding='same', subsample=(2,2))(conv4)
    pool4 = Dropout(0.5)(pool4)
    
    conv5 = inception_block(pool4, 512, batch_mode=2, splitted=splitted, activation=act)
    #conv5 = inception_block(conv5, 512, batch_mode=2, splitted=splitted, activation=act)
    conv5 = Dropout(0.5)(conv5)
    
    #
    pre = Conv2D(1, 1, strides=1, activation='sigmoid')(conv5)
    pre = Flatten()(pre)
    aux_out = Dense(1, activation='sigmoid', name='aux_output')(pre) 
    #
    after_conv4 = rblock(conv4, 1, 256)
    up6 = concatenate([UpSampling2D(size=(2, 2), data_format="channels_last")(conv5), after_conv4], axis=3)
    conv6 = inception_block(up6, 256, batch_mode=2, splitted=splitted, activation=act)
    conv6 = Dropout(0.5)(conv6)
    
    after_conv3 = rblock(conv3, 1, 128)
    up7 = concatenate([UpSampling2D(size=(2, 2), data_format="channels_last")(conv6), after_conv3], axis=3)
    conv7 = inception_block(up7, 128, batch_mode=2, splitted=splitted, activation=act)
    conv7 = Dropout(0.5)(conv7)
    
    after_conv2 = rblock(conv2, 1, 64)
    up8 = concatenate([UpSampling2D(size=(2, 2), data_format="channels_last")(conv7), after_conv2], axis=3)
    conv8 = inception_block(up8, 64, batch_mode=2, splitted=splitted, activation=act)
    conv8 = Dropout(0.5)(conv8)
    
    after_conv1 = rblock(conv1, 1, 32)
    up9 = concatenate([UpSampling2D(size=(2, 2), data_format="channels_last")(conv8), after_conv1], axis=3)
    conv9 = inception_block(up9, 32, batch_mode=2, splitted=splitted, activation=act)
    #conv9 = inception_block(conv9, 32, batch_mode=2, splitted=splitted, activation=act)
    conv9 = Dropout(0.5)(conv9)

    conv10 = Conv2D(1, 1, strides=1, activation='sigmoid', name='main_output')(conv9)
    #print conv10._keras_shape

    model = Model(inputs=[inputs], outputs=[conv10, aux_out])
    model.compile(optimizer=optimizer,
                  loss={'main_output': dice_coef_loss, 'aux_output': 'binary_crossentropy'},
                  metrics={'main_output': dice_coef, 'aux_output': 'acc'},
                  loss_weights={'main_output': 1., 'aux_output': 0.5})

    return model


get_unet = get_unet_inception_2head

def main():
    from keras.optimizers import Adam, RMSprop, SGD
    from metric import dice_coef, dice_coef_loss
    import numpy as np
    img_rows = IMG_ROWS
    img_cols = IMG_COLS
    
    optimizer = RMSprop(lr=0.045, rho=0.9, epsilon=1.0)
    model = get_unet(Adam(lr=1e-5))
    model.compile(optimizer=optimizer, loss=dice_coef_loss, metrics=[dice_coef])
    
    x = np.random.random((1, img_rows, img_cols, 1))
    res = model.predict(x, 1)
    print (res)
    #print 'res', res[0].shape
    print ('params', model.count_params())
    print ('layer num', len(model.layers))
    #


if __name__ == '__main__':
    sys.exit(main())

