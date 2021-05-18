#!/home/work/Desktop/workspace/electrumx-ravencoin/env/bin/python

import argparse
import kawpow

parser = argparse.ArgumentParser(description='KAWPOW hash utility')
parser.add_argument('header_hash',type=str,help="Block header hash (sha256d)")
parser.add_argument('mix_hash',type=str,help="Mix hash")
parser.add_argument('nonce',help="Nonce")
parser.add_argument('--expected_hash',type=str,default="",help="Expected final hash")
args=parser.parse_args()

nonce = int(args.nonce,0)

print(f"header_hash: {args.header_hash}")
print(f"mix_hash: {args.mix_hash}")
print(f"nonce: {nonce}")

final_hash = kawpow.light_verify(bytes.fromhex(args.header_hash), bytes.fromhex(args.mix_hash), nonce)

print(f"final_hash: {final_hash.hex()}")

if args.expected_hash != "":
    print(f"expected final_hash: {args.expected_hash}")
    if f != bytes.fromhex(args.expected_hash):
        print("error: final hash does not equal expected hash!")
    else:
        print("OK")
