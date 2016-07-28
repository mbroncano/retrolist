#!/usr/bin/python

from __future__ import print_function
import sys
import crcmod
from os import listdir, chdir, getcwd
from os.path import isfile, join, basename, splitext
import zipfile
import zlib
import shutil

debug=1

# debug, prints to stderr
def eprint(*args, **kwargs):
	if debug:
		print(*args, file=sys.stderr, **kwargs)
	return

#import hashlib
#def md5(fname):
#	hash_md5 = hashlib.md5()
#	with open(fname, "rb") as f:
#		for chunk in iter(lambda: f.read(4096), b""):
#			hash_md5.update(chunk)
#	return hash_md5.hexdigest()

# return the crc32 for a file
def crc(fname):
	hash_crc = crcmod.Crc(0x104c11db7, initCrc=0, xorOut=0xFFFFFFFF)
	with open(fname, "rb") as f:
		# ines detection, skip 16 first bytes
		nes_header = f.read(16)
		if nes_header[:4] == "\x4e\x45\x53\x1a":
			eprint('(nes) {}x16kB ROM, {}x8kB VROM'.format(ord(nes_header[5]), ord(nes_header[6])))
		else:
			hash_crc.update(nes_header)

		for chunk in iter(lambda: f.read(4096), b""):
			hash_crc.update(chunk)
	return hash_crc.hexdigest()

def verify_file(fname, crc_res):
	# compute the crc and search for it in the database
	game = root.find('.//rom[@crc="' + crc_res + '"]/..')
	if game is None:
		eprint('(not found) path: ' + fname + ', crc: ' + crc_res)
		return

	game_name = game.attrib['name']
	eprint('(found) name: ' + game_name)
	fpath = join(rompath, fname)
	crc_file[crc_res] = fpath

	# check if it's a clone, set the parent accordingly
	if 'cloneof' in game.attrib:
		parent = root.find('.//game[@name="' + game.attrib['cloneof'] + '"]')
		eprint(' - parent: ' + parent.attrib['name'])
	else:
		parent = game

	# check if the new candidate has a release, skip if not
	new_regions = map(lambda r: r.attrib['region'], game.findall('.//release'))
	if not len(new_regions): 
		eprint(' - no regions to compare! (and there is already a candidate)')
		return

	# filter new candidate for supported regions
	isect_regions = list(set(new_regions) & set(region_preference))
	if not len(isect_regions):
		eprint(' - no supported regions!')
		return
		
	# check if the there is no candidate already, add one
	# note: we only add a candidate that contains a acceptable release
	parent_name = parent.attrib['name']
	if parent_name not in pname_candidate:
		pname_candidate[parent_name] = (game, fpath, crc_res)
		eprint(' # added candidate: ' + game_name)
		return

	# get lowest index for region in new candidate
	new_index = reduce(min, map(lambda r: region_preference.index(r), isect_regions))

	# get supported regions for the current candidate 
	(current_game, _, _) = pname_candidate[parent_name]
	current_regions = filter(lambda r: r in region_preference, map(lambda r: r.attrib['region'], current_game.findall('.//release')))
	if not len(current_regions):
		current_index = 99999999
	else:
		current_index = reduce(min, map(lambda r: region_preference.index(r), current_regions))

	# replace the current candidate
	if new_index < current_index:
		eprint(' % replaced candidate: ' + current_game.attrib['name'])
		eprint(' % with new candidate: ' + game_name)
		pname_candidate[parent_name] = (game, fpath, crc_res)

# TODO: add parameter parsing
if len(sys.argv) < 3:
	print(sys.argv[0] + ': invalid number of arguments')
	print('usage: ' + sys.argv[0] + ' playlist prefix database rompath ...')
	exit(1)

# TODO: specify this as a parameter
#region_preference = ['USA', 'EUR', 'JPN']
region_preference = ['USA', 'EUR'] # filter out other regions, prefer USA over EUR

# read the database file
print('loading database ...')
import xml.etree.ElementTree as ET

tree = ET.parse(sys.argv[3])
root = tree.getroot()

# compute the crc for all files in every rompath
print('processing roms ...')

# TODO: remove globals
crc_file = {}
pname_candidate = {}

for rompath in sys.argv[4:]:
	# lists all files within the directory
	eprint('(path) loading roms from ' + rompath)
	cwd = getcwd()
	chdir(rompath)

	for fname in filter(isfile, listdir('.')):
		# process zip file as if it was a directory
		if zipfile.is_zipfile(fname):
			eprint('zip: ' + fname)
			zf = zipfile.ZipFile(fname)
			for zipinfo in zf.infolist():
				eprint(' * filename: ' + zipinfo.filename)
				eprint(' * crc: {:X}'.format(zipinfo.CRC))
				verify_file(fname + '#' + zipinfo.filename, '{:X}'.format(zipinfo.CRC))
		else:
			eprint('file: ' + fname)
			verify_file(fname, crc(fname))
	chdir(cwd)

print("writing playlist ...")

# generate retroarch playlist
playlist = sys.argv[1]
file_prefix = sys.argv[2]
with open(playlist, 'w') as p:
	for pname in sorted(pname_candidate):
		(_, path, crc) = pname_candidate[pname]

		# check if the file is already archived
		path_split = path.split('.zip#')
		if len(path_split) == 1:
			# create a zip file in the current directory with the rom
			zip_path = splitext(basename(path))[0] + '.zip'
			archive_path = join(file_prefix, zip_path) + '#' + basename(path)
		
			print('writing file: ' + zip_path)
			zf = zipfile.ZipFile(zip_path, mode='w')
			zf.write(path, arcname=basename(path), compress_type=zipfile.ZIP_DEFLATED)
			zf.close()
		else:
			# copy the zip file to the current directory
			zip_path = path_split[0] + '.zip'
			print('copying file: ' + zip_path)
			shutil.copy(zip_path, '.')
			archive_path = path

		# append new entry to the playlist
		p.write(archive_path + '\n')
		p.write(pname + '\n')
		p.write('DETECT\n')
		p.write('DETECT\n')
		p.write(crc + '|crc\n')
		p.write(playlist + '\n')


print("done!")
