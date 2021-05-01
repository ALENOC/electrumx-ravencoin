'''Module providing asset utilities.

Anything asset-specific should go in this file.

Most of this is taken from the Raven Core's script.cpp
'''

TX_TRANSFER_ASSET = 0
TX_NEW_ASSET = 1
TX_REISSUE_ASSET = 2

RVN_R = 114
RVN_V = 118
RVN_N = 110
RVN_Q = 113
RVN_T = 116
RVN_O = 111

OP_RVN_ASSET = 0xc0
OP_RESERVED = 0x50

def is_new_asset(script: bytes) -> bool:
    data = is_asset_script(script)
    if data is None:
        return False
    return data[0] == TX_NEW_ASSET and not data[1]

def is_owner_asset(script: bytes) -> bool:
    data = is_asset_script(script)
    if data is None:
        return False
    return data[0] == TX_NEW_ASSET and data[1]

def is_reissue_asset(script: bytes) -> bool:
    data = is_asset_script(script)
    if data is None:
        return False
    return data[0] == TX_REISSUE_ASSET

def is_transfer_asset(script: bytes) -> bool:
    data = is_asset_script(script)
    if data is None:
        return False
    return data[0] == TX_TRANSFER_ASSET

def is_null_asset(script: bytes) -> bool:
    return is_null_asset_tx_data_script(script) or \
           is_null_global_restriction_asset_tx_data_script(script) or \
           is_null_asset_verifier_tx_data_script(script)


def is_null_asset_tx_data_script(script: bytes) -> bool:
    return len(script) > 23 and script[0] == OP_RVN_ASSET and script[1] == 0x14

def is_null_global_restriction_asset_tx_data_script(script: bytes) -> bool:
    return len(script) > 6 and script[0] == OP_RVN_ASSET and script[1] == OP_RESERVED and script[2] == OP_RESERVED

def is_null_asset_verifier_tx_data_script(script: bytes) -> bool:
    return len(script) > 3 and script[0] == OP_RVN_ASSET and script[1] == OP_RESERVED and script[2] != OP_RESERVED

def is_asset_script(script: bytes):
    '''
    Returns a tuple of (type, is owner?, starting index)
    if is a valid asset script

    Returns None otherwise.
    '''

    if len(script) > 31:
        if script[25] == OP_RVN_ASSET:
            index = -1
            #TODO: Optimize for python
            if script[27] == RVN_R:
                if script[28] == RVN_V:
                    if script[29] == RVN_N:
                        index = 30
            elif script[28] == RVN_R:
                if script[29] == RVN_V:
                    if script[30] == RVN_N:
                        index = 31

            if index > 0:
                if script[index] == RVN_T:
                    return TX_TRANSFER_ASSET, False, (index+1)
                elif script[index] == RVN_Q and len(script) > 39:
                    return TX_NEW_ASSET, False, (index+1)
                elif script[index] == RVN_O:
                    return TX_NEW_ASSET, True, (index+1)
                elif script[index] == RVN_R:
                    return TX_REISSUE_ASSET, False, (index+1)

    return None