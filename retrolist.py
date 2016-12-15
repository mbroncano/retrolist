#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Generate a romset and play list for a given system and rom collection"""

import sys
from os import listdir, chdir, getcwd, makedirs
from os.path import isfile, join, basename, exists
import zipfile
import xml.etree.ElementTree as ET
import array
import time
import argparse
import logging
import io
import cPickle
import hashlib
import crcmod
import py7zlib


def display_message(text):
    """Display message in the standard output"""
    if logging.getLogger().getEffectiveLevel() != logging.DEBUG:
        sys.stdout.write(text)
        sys.stdout.flush()


def display_step(text, value):
    """Display a process step"""
    display_message('{0}:\t"{1}"\n'.format(text, value))


def display_progress(text, index, total, length=40):
    """Display a progress bar for a process"""
    index += 1
    bar_progress = length*index/total
    bar_remaining = length-bar_progress
    text = '\r{0}:\t[{1}] ({2}/{3})'.format(text, '#'*bar_progress+'-'*bar_remaining, index, total)
    display_message(text)
    if index >= total:
        display_message('\n')


def is_game_name(name):
    """ Identifies a Beta, BIOS, Prototype game """
    return any(s in name for s in ['[BIOS]', '(Proto)', '(SDK Build)'])

def is_ines(header):
    """ Detects whether this is a iNES rom """
    if header[:4] == "\x4e\x45\x53\x1a": # "NES^Z"
        logging.debug('(nes) %dx16kB ROM, %dx8kB VROM', ord(header[5]), ord(header[6]))
        return True
    return False


def is_n64(header):
    """Detect a n64 file type"""
    if header[:4] == "\x40\x12\x37\x80":
        logging.debug('(n64) detected .n64')
        exit(2)
        return True
    return False


def is_z64(header):
    """Detect a z64 file type"""
    if header[:4] == "\x80\x37\x12\x40":
        logging.debug('(n64) detected .z64')
        return True
    return False


def is_v64(header):
    """Detect a v64 file type"""
    if header[:4] == "\x37\x80\x40\x12":
        logging.debug('(n64) detected .v64')
        return True
    return False


def n64_correct(bytelist, swap):
    """Correct a n64 file block"""
    buf = array.array(swap, bytelist)
    buf.byteswap()
    return buf.tostring()


def crc_file(fobj):
    """ Computes CRC32 for a file object """

    hash_crc = crcmod.Crc(0x104c11db7, initCrc=0, xorOut=0xFFFFFFFF)
    header = fobj.read(16)

    swap = None
    if is_z64(header):
        swap = "H"
    elif is_n64(header):
        swap = "I"
    elif is_v64(header):
        logging.debug('(n64) no correction required')

    # ines detection, skip 16 first bytes
    if not is_ines(header):
        if swap is not None:
            header = n64_correct(header, swap)
        hash_crc.update(header)

    for chunk in iter(lambda: fobj.read(4096), b""):
        if swap is not None:
            chunk = n64_correct(chunk, swap)
        hash_crc.update(chunk)
    return hash_crc.hexdigest()


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
    with open(fname, "rb") as fobj:
        return crc_file(fobj)


def verify_file(fname, rompath, crc_res, dbroot, pname_candidate, region_preference, include_bios):
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

    # check if the game has a crc checksum associated
    game = dbroot.find('.//rom[@crc="' + crc_res + '"]/..')
    if game is None:
        logging.debug('(crc) not found! path: %s, crc: %s', fname, crc_res)
        return

    # extract the file name
    game_name = game.attrib['name']
    logging.debug('(crc) file matches game: ' + game_name)
    fpath = join(rompath, fname)

    # check if this is a bios file
    if not include_bios and is_game_name(game_name):
        logging.debug('(crc) - game name is either bios or prototype, skipping ...')
        return

    # check if it's a clone, set the parent accordingly
    if 'cloneof' in game.attrib:
        parent = dbroot.find('.//game[@name="' + game.attrib['cloneof'] + '"]')
        logging.debug('(crc) - parent: ' + parent.attrib['name'])
    else:
        parent = game

    # check if the new candidate has a release, skip if not
    new_regions = [r.attrib['region'] for r in game.findall('.//release')]
    if not len(new_regions):
        logging.debug('(crc) - no regions to compare! (and there is already a candidate)')
        return

    # list supported regions for candidate
    if region_preference:
        isect_regions = list(set(new_regions) & set(region_preference))
        if not len(isect_regions):
            logging.debug('(crc) - no supported regions!')
            return
        logging.debug('(crc) - regions supported: %s', ', '.join(isect_regions))
    else:
        isect_regions = new_regions
        logging.debug('(crc) - regions available: %s', ', '.join(isect_regions))

    # check if the there is no candidate already, add one
    # note: we only add a candidate that contains a acceptable release
    parent_name = parent.attrib['name']
    if parent_name not in pname_candidate:
        pname_candidate[parent_name] = (game, fpath, crc_res)
        logging.debug('(crc) - added candidate: ' + game_name)
        return

    # if we don't filter regions, we always take the first candidate
    if not len(region_preference):
        logging.debug('(crc) - no region set specified, ignoring candidate: %s', game_name)
        return

    # get lowest index for region in new candidate
    new_index = reduce(min, [region_preference.index(r) for r in isect_regions])

    # get supported regions for the current candidate
    (current_game, _, _) = pname_candidate[parent_name]
    current_releases = current_game.findall('.//release')
    current_regions = [r.attrib['region'] for r in current_releases if r in region_preference]

    # get the priority of the current region
    current_priorities = [region_preference.index(r) for r in current_regions] + [sys.maxsize]
    current_index = reduce(min, current_priorities)

    # this candidate has less priority
    if new_index >= current_index:
        logging.debug('(crc) - inferior region priority, ignoring candidate: %s', game_name)
        return

    # replace the current candidate
    logging.debug('(crc) - replaces candidate: %s', current_game.attrib['name'])
    pname_candidate[parent_name] = (game, fpath, crc_res)


def load_database(dbpath, region_priority, region_filter):
    """ Loads a parent/clone xml dat-o-matic type database

    Args:
        dbpath(str): the database file path

    Returns:
        xml.etree.ElementTree: the root of the parsed XML tree
        list: a list of supported regions sorted by priority
    """

    # read the database file
    display_step('load dat', args.database)
    tree = ET.parse(dbpath)
    root = tree.getroot()

    # extract some useful information
    games = root.findall('game')
    clones = root.findall('game[@cloneof]')
    region_list = sorted(set([x.attrib['region'] for x in root.findall('.//release')]))
    bios = [game for game in game if not is_game_name(game.attrib['name'])]

    # display some debug info
    logging.debug('(xml) path: %s', dbpath)
    logging.debug('(xml) - name: %s', root.find('.//header/name').text)
    logging.debug('(xml) - desc: %s', root.find('.//header/description').text)
    logging.debug('(xml) - games: total %d (%d parent / %d clones)', \
        len(games), len(games)-len(clones), len(clones))
    logging.debug('(xml) - regions: total %d (%s)', len(region_list), ', '.join(region_list))
    logging.debug('(xml) - bios, beta, prototypes: total %d', len(bios))

    # add regions not in the priority list but present in the data set
    if len(region_priority):
        region_list = region_priority + list(set(region_list) - set(region_priority))
        logging.debug('(xml) - priority: %s', ', '.join(region_list))

    # remove filtered regions from the priority list
    if len(region_filter):
        region_list = list(set(region_list) & set(region_filter))
        logging.debug('(xml) - filter: %s', ', '.join(region_list))

    return root, region_list


def verify_archive(fname, fp, dbroot, regions, bios_filter, rompath, pname_candidate):
    logging.debug('(arc) verifying: ' + fname)

    try:
        zfile = zipfile.ZipFile(fp)
        logging.debug('(zip) zip archive detected')
        for zipinfo in zfile.infolist():
            logging.debug('(zip) filename: ' + zipinfo.filename)
            logging.debug('(zip) - digest: {:X}'.format(zipinfo.CRC))
            with io.BytesIO(zfile.read(zipinfo)) as new_fp:
                new_fname = fname + '#' + zipinfo.filename
                verify_archive(new_fname, new_fp, dbroot, regions, bios_filter, rompath, pname_candidate)

    except zipfile.BadZipfile:
        pass
        #logging.debug('(zip) not a zip file')

    fp.seek(0)
    try:
        ar7z = py7zlib.Archive7z(fp)
        logging.debug('(p7z) 7z archive detected')
        for fp7z in ar7z.getmembers():
            logging.debug('(p7z) filename: ' + fp7z.filename)
            logging.debug('(p7z) - digest: {:X}'.format(fp7z.digest))
            with io.BytesIO(fp7z.read()) as new_fp:
                new_fname = fname + '#' + fp7z.filename
                verify_archive(fname, new_fp, dbroot, regions, bios_filter, rompath, pname_candidate)

    except py7zlib.FormatError:
        pass
        #logging.debug('(p7z) not a 7z file')

    # for iNES the computed CRC differs
    fp.seek(0)
    crc_res = crc_file(fp)
    verify_file(fname, rompath, crc_res, dbroot, pname_candidate, regions, bios_filter)


def verify_paths(path_list, dbroot, region_l, bios_filter):
    """ Verifies a list of paths for valid ROMS against a database

    Args:
        path_list(list): a list of directories
        dbroot(ElementTree): the root of the parsed XML tree

    Returns:
        dict: a dictionary with the candidate (game, fname, crc) for each game found
    """

    candidate_dict = dict()
    for rompath in path_list:
        display_step('check path', rompath)

        # lists all files within the directory
        logging.debug('(rom) loading roms from path: ' + rompath)

        # save current directory, change to the next one
        cwd = getcwd()
        chdir(rompath)

        filelist = filter(isfile, listdir('.'))
        for idx, fname in enumerate(filelist):
            display_progress("verify path", idx, len(filelist))

            # check for archive file
            logging.debug('(rom) checking for archive: ' + fname)
            with open(fname) as fobj:
                verify_archive(fname, fobj, dbroot, region_l, bios_filter, rompath, candidate_dict)

        # return to previous directory
        chdir(cwd)

    return candidate_dict


def generate_playlist(candidate_dict, playlist, file_prefix):
    """ Generates a Retroarch compatible playlist

    Args:
        candidate_dict(dict): a dictionary containing a romset
        playlist(string): the playlist file path
        file_prefix(string): the base path for games in the playlist

    """
    with open(playlist, 'w') as fobj:
        logging.debug('(lpl) creating playlist: ' + playlist)
        for idx, pname in enumerate(sorted(pname_candidate)):
            display_progress("create plist", idx, len(pname_candidate))

            # retrieve game path and crc for a game
            (_, path, file_crc) = candidate_dict[pname]
            if file_prefix:
                path = join(file_prefix, basename(path))

            # append new entry to the playlist
            logging.debug('(lpl) - adding game: %s', pname)
            entry = "{}\n{}\nDETECT\nDETECT\n{}|crc\n{}\n".format(path, pname, file_crc, playlist)
            fobj.write(entry)


def create_romset(pname_candidate, dest='.'):
    """ Creates a romset out of a the games found and matched """
    for idx, pname in enumerate(sorted(pname_candidate)):
        display_progress("create romset", idx, len(pname_candidate))

        (game, path, crc) = pname_candidate[pname]

        # retrieve the rom and game name
        rom = game.find('./rom[@crc="' + crc + '"]')
        rname = rom.attrib['name']
        gname = game.attrib['name']
        size = rom.attrib['size']

        logging.debug('(set) processing game: ' + gname)
        logging.debug('(set) - rom: ' + rname) 
        logging.debug('(set) - crc: ' + crc) 
        logging.debug('(set) - size: ' + size) 
        logging.debug('(set) - path: ' + path)

        # check if the file is inside of a zip
        path_split = path.split('.zip#')
        if len(path_split) == 1:
            # open the file
            fo = open(path)
        else:
            # open the zip and the file
            zo = zipfile.ZipFile(path_split[0] + '.zip', mode='r')
            fo = zo.open(path_split[1])

        # create a zip file in the destination directory with the rom name
        zip_path = join(dest, gname + '.zip')
        logging.debug('(set) - archive: ' + zip_path)
        zf = zipfile.ZipFile(zip_path, mode='w')

        # use the rom name from the dat file also for the zipped file
        zi = zipfile.ZipInfo(rname)
        zi.date_time = time.localtime(time.time())[:6]
        zi.compress_type = zipfile.ZIP_DEFLATED

        # read whole file in memory (zip doesn't like appending)
        fstr = fo.read()

        # convert format for n64
        swap = None
        if is_z64(fstr[:8]):
            swap = "H"
        elif is_n64(fstr[:8]):
            swap = "I"
        elif is_v64(fstr[:8]):
            logging.debug('(n64) - no correction required')

        if swap is not None:
            fstr = n64_correct(fstr, swap)
            logging.debug('(n64) - file corrected!')

        # write file
        logging.debug('(set) - adding game: ' + gname)
        zf.writestr(zi, fstr)
        zf.close()
        logging.debug('(set) - archive closed correctly')


if __name__ == '__main__':

    # parse the given parameters in the command line
    __parser__ = argparse.ArgumentParser(description='Libretro romset manager')
    __parser__.add_argument('database', \
        help='the parent/clone xml database from no-intro.org')
    __parser__.add_argument('rompath', \
        nargs='+',  \
        help='the directories containing the rom files to be processed')
    __parser__.add_argument('-r', '--romdir', \
        help='create a romset in the specified directory with the game set', \
        action='store_true')
    __parser__.add_argument('-l', '--playlist', \
        help='create a playlist file with the game set', \
        action='store_true')
    __group__ = __parser__.add_mutually_exclusive_group()
    __group__.add_argument('-p', '--priority', \
        nargs='+', \
        help='a list of the regions that will be prioritized', \
        default=['USA', 'EUR', 'JPN'])
    __group__.add_argument('-f', '--filter', \
        nargs='+', \
        help='a list of the regions that will be included', \
        default=[])
    __parser__.add_argument('-x', '--prefix', \
        help='the base path for the files in the playlist (e.g. /storage/roms/ for lakka.tv)')
    __parser__.add_argument('-b', '--bios', \
        help='include games whose name contain the string [BIOS] in the game set', \
        action='store_true')
    __parser__.add_argument('--verbose', '-v', \
        action='count', \
        help='display verbose output')
    args = __parser__.parse_args()

    # set logging level
    if args.verbose > 0:
        logging.basicConfig(stream=sys.stderr, level=logging.DEBUG)

     # load the database and compute the checksum for the given paths
    xml_root, regions = load_database(args.database, args.priority, args.filter)

    # compute checksum
    pname_candidate = None
    arguments = [args.database, args.rompath, args.priority, args.filter, args.bios]
    cache_filename = '/tmp/' + hashlib.md5(cPickle.dumps(arguments)).hexdigest() + '.cache'
    try:
        with open(cache_filename, 'r') as dump:
            display_step('load cache', cache_filename)
            logging.debug('(pic) loading cache file ...')
            pname_candidate = cPickle.loads(dump.read())
            logging.debug('(pic) cache loaded!')
    except:
        logging.debug('(pic) error loading cache!')

    if pname_candidate is None:
        pname_candidate = verify_paths(args.rompath, xml_root, regions, args.bios)
        with open(cache_filename, 'w') as dump:
            display_step('save cache', cache_filename)
            logging.debug('(pic) writing cache file ...')
            dump.write(cPickle.dumps(pname_candidate))
            logging.debug('(pic) cache written!')

    # generate the retroarch playlist
    system_name = xml_root.find('.//header/name').text.split(' Parent-Clone')[0]
    if args.playlist:
        playlist_filename = system_name + ".lpl"
        generate_playlist(pname_candidate, playlist_filename, args.prefix or '')

    # create the romset
    if args.romdir:
        # create the directory if it doesn't exist
        romdir = system_name
        if not exists(romdir):
            makedirs(romdir)
        create_romset(pname_candidate, romdir)

