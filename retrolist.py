#!/usr/bin/python

import sys
import crcmod
from os import listdir, chdir, getcwd, makedirs
from os.path import isfile, join, basename, splitext, exists
import zipfile
import zlib
import shutil
import xml.etree.ElementTree as ET
import array
import time
import argparse
import logging
import py7zlib
import io

logging.basicConfig(stream=sys.stderr, level=logging.DEBUG)

def is_ines(header):
    """ Detects whether this is a iNES rom """
    if header[:4] == "\x4e\x45\x53\x1a": # "NES^Z"
        logging.debug('(nes) {}x16kB ROM, {}x8kB VROM'.format(ord(header[5]), ord(header[6])))
        return True
    return False


def is_n64(header):
    if header[:4] == "\x40\x12\x37\x80":
        logging.debug('(n64) detected .n64')
        exit(2)
        return True
    return False


def is_z64(header):
    if header[:4] == "\x80\x37\x12\x40":
        logging.debug('(n64) detected .z64')
        return True
    return False


def is_v64(header):
    if header[:4] == "\x37\x80\x40\x12":
        logging.debug('(n64) detected .v64')
        return True
    return False


def n64_correct(bytelist, swap):
    buf = array.array(swap, bytelist)
    buf.byteswap()
    return buf.tostring()


def crc_file(f):
    """ Computes CRC32 for a file object """

    hash_crc = crcmod.Crc(0x104c11db7, initCrc=0, xorOut=0xFFFFFFFF)
    header = f.read(16)

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

    for chunk in iter(lambda: f.read(4096), b""):
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
    with open(fname, "rb") as f:
        return crc_file(f)


def verify_file(fname, rompath, crc_res, dbroot, pname_candidate, region_preference):
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
        logging.debug('(crc) not found! path: ' + fname + ', crc: ' + crc_res)
        return

    game_name = game.attrib['name']
    logging.debug('(crc) file matches game: ' + game_name)
    fpath = join(rompath, fname)

    # check if it's a clone, set the parent accordingly
    if 'cloneof' in game.attrib:
        parent = dbroot.find('.//game[@name="' + game.attrib['cloneof'] + '"]')
        logging.debug('(crc) - parent: ' + parent.attrib['name'])
    else:
        parent = game

    # check if the new candidate has a release, skip if not
    new_regions = map(lambda r: r.attrib['region'], game.findall('.//release'))
    if not len(new_regions):
        logging.debug('(crc) - no regions to compare! (and there is already a candidate)')
        return

    # filter new candidate for supported regions
    if region_preference:
      isect_regions = list(set(new_regions) & set(region_preference))
      if not len(isect_regions):
        logging.debug('(crc) - no supported regions!')
        return
      else:
        logging.debug('(crc) - regions supported: ' + ', '.join(isect_regions))
    else:
      isect_regions = new_regions
      logging.debug('(crc) - regions available: ' + ', '.join(isect_regions))

    # check if the there is no candidate already, add one
    # note: we only add a candidate that contains a acceptable release
    parent_name = parent.attrib['name']
    if parent_name not in pname_candidate:
        pname_candidate[parent_name] = (game, fpath, crc_res)
        logging.debug('(crc) - added candidate: ' + game_name)
        return
    
    # if we don't filter regions, we always take the first candidate
    if not len(region_preference):
      logging.debug('(crc) - no region set specified, ignoring candidate: ' + current_game.attrib['name'])
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

    # this candidate has less priority
    if new_index >= current_index:
        logging.debug('(crc) - inferior region priority, ignoring candidate: ' + game_name)
        return

    # replace the current candidate
    logging.debug('(crc) - replaces candidate: ' + current_game.attrib['name'])
    #logging.debug('(crc) - with new candidate: ' + game_name)
    pname_candidate[parent_name] = (game, fpath, crc_res)


def load_database(dbpath, region_priority, region_filter):
    """ Loads a parent/clone xml dat-o-matic type database

    Args:
        dbpath(str): the database file path

    Returns:
        xml.etree.ElementTree: the root of the parsed XML tree
    """

    # read the database file
    print('loading database: ' + args.database)
    tree = ET.parse(dbpath)
    root = tree.getroot()
   
    # extract some useful information 
    games = root.findall('game')
    clones = root.findall('game[@cloneof]')
    regions = sorted(set(map(lambda x: x.attrib['region'], root.findall('.//release'))))

    # print some debug info
    logging.debug('(xml) path: ' + dbpath);
    logging.debug('(xml) - name: ' + root.find('.//header/name').text)
    logging.debug('(xml) - desc: ' + root.find('.//header/description').text)
    logging.debug('(xml) - games: total {} ({} parent / {} clones)'.format(len(games), len(games)-len(clones), len(clones)))
    logging.debug('(xml) - regions: total {} ({})'.format(len(regions), ', '.join(regions)))

    if len(region_priority):
      regions = region_priority + list(set(regions) - set(region_priority))
      logging.debug('(xml) - priority: {}'.format(', '.join(regions)))
   
    if len(region_filter):
      regions = list(set(regions) & set(region_filter))
      logging.debug('(xml) - filter: {}'.format(', '.join(regions)))

    return root, regions


def verify_archive(fname, fp, dbroot, regions, rompath, pname_candidate):
    logging.debug('(arc) verifying: ' + fname)

    try:
        zf = zipfile.ZipFile(fp)
        logging.debug('(zip) zip archive detected')
        for zipinfo in zf.infolist():
            logging.debug('(zip) filename: ' + zipinfo.filename)
            logging.debug('(zip) - digest: {:X}'.format(zipinfo.CRC))
            fp = io.BytesIO(zipinfo.read())
            filename = fname + '#' + zipinfo.filename
            verify_archive(filename, fp, dbroot, regions, rompath, pname_candidate)

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
            fp = io.BytesIO(fp7z.read())
            filename = fname + '#' + fp7z.filename
            verify_archive(filename, fp, dbroot, regions, rompath, pname_candidate)

    except py7zlib.FormatError:
        pass
        #logging.debug('(p7z) not a 7z file')

    # for iNES the computed CRC differs
    fp.seek(0)
    crc_res = crc_file(fp)
    verify_file(fname, rompath, crc_res, dbroot, pname_candidate, regions)


def verify_paths(path_list, dbroot, regions):
    """ Verifies a list of paths for valid ROMS against a database

    Args:
        path_list(list): a list of directories
        dbroot(ElementTree): the root of the parsed XML tree

    Returns:
        dict: a dictionary with the candidate (game, fname, crc) for each game found
    """

    pname_candidate = dict()
    for rompath in path_list:
        print('processing path: ' + rompath)

        # lists all files within the directory
        logging.debug('(rom) loading roms from path: ' + rompath)
        cwd = getcwd()
        chdir(rompath)

        for fname in filter(isfile, listdir('.')):
            # check for archive file
            logging.debug('(rom) checking for archive: ' + fname)
            with open(fname) as fp:
                verify_archive(fname, fp, dbroot, regions, rompath, pname_candidate)
           
            continue

            # process zip file as if it was a directory
            if zipfile.is_zipfile(fname):
                logging.debug('(rom) checking files from zip archive: ' + fname)
                zf = zipfile.ZipFile(fname)
                for zipinfo in zf.infolist():
                    filename = zipinfo.filename
                    CRC = '{:X}'.format(zipinfo.CRC)
                    crc_res = crc_file(zf.open(zipinfo))

                    # for iNES the computed CRC differs
                    logging.debug('(zip) filename: ' + filename)
                    logging.debug('(zip) - crc (zip): ' + CRC)
                    logging.debug('(zip) - crc (nes): ' + crc_res)

                    verify_file(fname + '#' + zipinfo.filename, rompath, crc_res, dbroot, pname_candidate, regions)
            else:
                logging.debug('(rom) checking file name: ' + fname)
                verify_file(fname, rompath, crc(fname), dbroot, pname_candidate, regions)
        chdir(cwd)

    return pname_candidate


def generate_playlist(pname_candidate, playlist, file_prefix):
    """ Generates a Retroarch compatible playlist """
    print("writing playlist ...")
    with open(playlist, 'w') as p:
        logging.debug('(lpl) creating playlist: ' + playlist)
        for pname in sorted(pname_candidate):
            (_, path, crc) = pname_candidate[pname]

            if file_prefix:
              archive_path = join(file_prefix, basename(path))
            else:
              archive_path = path 

            # append new entry to the playlist
            p.write(archive_path + '\n')
            p.write(pname + '\n')
            p.write('DETECT\n')
            p.write('DETECT\n')
            p.write(crc + '|crc\n')
            p.write(playlist + '\n')

            logging.debug('(lpl) - adding game: ' + pname)


def create_romset(pname_candidate, dest='.'):
    """ Creates a romset out of a the games found and matched """
    print("creating romset ...")
    for pname in sorted(pname_candidate):
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
    parser = argparse.ArgumentParser(description='Libretro romset manager')
    parser.add_argument('database', help='the parent/clone xml database from no-intro.org')
    parser.add_argument('rompath', nargs='+', help='the directories containing the rom files to be processed')
    parser.add_argument('--playlist', help='the name of the playlist file')
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--romdir', help='the directory that will contain the romset')
    group.add_argument('--prefix', help='the base path for the files in the playlist')
    group2 = parser.add_mutually_exclusive_group()
    group2.add_argument('--priority', nargs='+', help='the regions that will be prioritized', default=['USA', 'EUR', 'JPN'])
    group2.add_argument('--filter', nargs='+', help='the regions that will be included', default=[])
    args = parser.parse_args()

    # load the database and compute the checksum for the given paths
    xml_root, regions = load_database(args.database, args.priority, args.filter)
    pname_candidate = verify_paths(args.rompath, xml_root, regions)

    # generate the retroarch playlist
    if args.playlist:
      if args.romdir:
        prefix = args.romdir
      else:
        prefix = args.prefix
      generate_playlist(pname_candidate, args.playlist, prefix)
   
    # create the romset
    if args.romdir:
      # create the directory if it doesn't exist
      if not exists(args.romdir):
        makedirs(args.romdir)
      create_romset(pname_candidate, args.romdir)

