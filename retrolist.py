#!/usr/bin/python

from __future__ import print_function
import sys
import crcmod
from os import listdir, chdir, getcwd
from os.path import isfile, join, basename, splitext
import zipfile
import zlib
import shutil
import xml.etree.ElementTree as ET

# TODO: specify this as a parameter
region_preference = ['USA', 'EUR'] # filter out other regions, prefer USA over EUR

debug=1

def eprint(*args, **kwargs):
	""" Debug print function """
	if debug:
		print(*args, file=sys.stderr, **kwargs)
	return

def crc(fname):
	""" Compute the CRC32 for a file.

	This function computes the CRC32 for a file. If the file is detected to be a iNES file
	(see http://wiki.nesdev.com/w/index.php/INES) then the header is not computed as part 
	of the check, as required by emulators.

	Args:
		fname(str): the file path

	Returns:
		str: a hexadecimal string for the computed value
	"""
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

def verify_file(fname, rompath, crc_res, dbroot, pname_candidate):
	""" Verifies that a file with a given CRC32 checksum matches a game entry in the database

	This function check that a given file name matches a corresponding entry to its given CRC32
	in the game parent/clone database. According to a number of aspects, it potentially replaces
	an existing entry in a global dictionary that selects at the most a single ROM for all games.

	Args:
		fname(str): the file path
		rompath(str): the directory name
		crc_res(str): a hexadecimal string with a valid CRC32 checksum 
		root(xml.etree.ElementTree): the parent/clone game database
		pname_candidate(dict): a dict with key 'parent game name' and value
			the triplet (game, fname, crc) of the current candidate

	Returns:
		nothing
	"""
	game = dbroot.find('.//rom[@crc="' + crc_res + '"]/..')
	if game is None:
		eprint('(not found) path: ' + fname + ', crc: ' + crc_res)
		return

	game_name = game.attrib['name']
	eprint('(found) name: ' + game_name)
	fpath = join(rompath, fname)

	# check if it's a clone, set the parent accordingly
	if 'cloneof' in game.attrib:
		parent = dbroot.find('.//game[@name="' + game.attrib['cloneof'] + '"]')
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

def load_database(dbpath):
	""" Loads a parent/clone xml dat-o-matic type database 

	Args:
		dbpath(str): the database file path
	
	Returns:
		xml.etree.ElementTree: the root of the parsed XML tree
	"""

	# read the database file
	tree = ET.parse(dbpath)
	return tree.getroot()

def verify_paths(path_list, dbroot):
	""" Verifies a list of paths for valid ROMS against a database

	Args:
		path_list(list): a list of directories

	Returns:
		dict: a dictionary with the candidate (game, fname, crc) for each game found
	"""

	pname_candidate = dict() 
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
					# TODO: this won't work for iNES files
					verify_file(fname + '#' + zipinfo.filename, rompath, '{:X}'.format(zipinfo.CRC), dbroot, pname_candidate)
			else:
				eprint('file: ' + fname)
				verify_file(fname, rompath, crc(fname), dbroot, pname_candidate)
		chdir(cwd)

	return pname_candidate

def generate_playlist(pname_candidate, playlist, file_prefix):
	""" Generates a Retroarch compatible playlist """
	with open(playlist, 'w') as p:
		for pname in sorted(pname_candidate):
			(_, path, crc) = pname_candidate[pname]

			archive_path = join(file_prefix, path)

			# append new entry to the playlist
			p.write(archive_path + '\n')
			p.write(pname + '\n')
			p.write('DETECT\n')
			p.write('DETECT\n')
			p.write(crc + '|crc\n')
			p.write(playlist + '\n')

def create_romset(pname_candidate, dest='.'):
	""" Creates a romset out of a the games found and matched """
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
			shutil.copy(zip_path, dest)
			archive_path = path


# TODO: add parameter parsing
if len(sys.argv) < 3:
	print(sys.argv[0] + ': invalid number of arguments')
	print('usage: ' + sys.argv[0] + ' playlist prefix database rompath ...')
	exit(1)


print('loading database ...')
xml_root=load_database(sys.argv[3])

print('processing roms ...')
pname_candidate=verify_paths(sys.argv[4:], xml_root)

print("writing playlist ...")
generate_playlist(pname_candidate, sys.argv[1], sys.argv[2])

print("creating romset ...")
create_romset(pname_candidate)

print("done!")
