#!/usr/bin/env python

from pathlib import Path
from argparse import ArgumentParser, Namespace, ArgumentDefaultsHelpFormatter
from loguru import logger
from chris_plugin import chris_plugin, PathMapper
import pfdcm
import json
import sys
import pprint
import os
from typing import Set
from pynetdicom import (
    AE,
    StoragePresentationContexts,
    build_role,
    evt,
)
from pynetdicom.sop_class import (
    PatientRootQueryRetrieveInformationModelFind,
    PatientRootQueryRetrieveInformationModelMove,
    StudyRootQueryRetrieveInformationModelFind,
    StudyRootQueryRetrieveInformationModelMove,
)
from pydicom.dataset import Dataset
from pydicom.uid import ExplicitVRLittleEndian, ImplicitVRLittleEndian

LOG = logger.debug

logger_format = (
    "<green>{time:YYYY-MM-DD HH:mm:ss}</green> │ "
    "<level>{level: <5}</level> │ "
    "<yellow>{name: >28}</yellow>::"
    "<cyan>{function: <30}</cyan> @"
    "<cyan>{line: <4}</cyan> ║ "
    "<level>{message}</level>"
)
logger.remove()
logger.add(sys.stderr, format=logger_format)
__version__ = '1.0.7'

DISPLAY_TITLE = r"""
       _                                        _        _                
      | |                                      | |      (_)               
 _ __ | |______ _ __   __ _  ___ ___   _ __ ___| |_ _ __ _  _____   _____ 
| '_ \| |______| '_ \ / _` |/ __/ __| | '__/ _ \ __| '__| |/ _ \ \ / / _ \
| |_) | |      | |_) | (_| | (__\__ \ | | |  __/ |_| |  | |  __/\ V /  __/
| .__/|_|      | .__/ \__,_|\___|___/ |_|  \___|\__|_|  |_|\___| \_/ \___|
| |            | |                ______                                  
|_|            |_|               |______|                                 
"""


parser = ArgumentParser(description='A plugin to retrieve DICOM images from a remote PACS',
                        formatter_class=ArgumentDefaultsHelpFormatter)
parser.add_argument(
    '--inputJSONfile',
    default='',
    type=str,
    help='name of the JSON file containing DICOM data to be retrieved'
)
parser.add_argument(
    '--copyInputFile',
    default=False,
    action="store_true",
    help='If specified, copy input JSON to output dir'
)
parser.add_argument(
        "--query-model",
        default="study",
        choices=["study", "patient"],
        help="Query/Retrieve model",
    )
# PACS / network
parser.add_argument("--src-aet", required=True, help="Called AET  (PACS AE title)")
parser.add_argument("--src-ip", required=True, help="PACS host / IP address")
parser.add_argument("--src-port", required=True, type=int, help="PACS DICOM port")
parser.add_argument("--dst-aet", required=True, help="Calling AET (our AE title, must be registered on PACS)")
parser.add_argument('-V', '--version', action='version',
                    version=f'%(prog)s {__version__}')


# The main function of this *ChRIS* plugin is denoted by this ``@chris_plugin`` "decorator."
# Some metadata about the plugin is specified here. There is more metadata specified in setup.py.
#
# documentation: https://fnndsc.github.io/chris_plugin/chris_plugin.html#chris_plugin
@chris_plugin(
    parser=parser,
    title='A ChRIS plugin to retrieve from a remote PACS ',
    category='',                 # ref. https://chrisstore.co/plugins
    min_memory_limit='100Mi',    # supported units: Mi, Gi
    min_cpu_limit='1000m',       # millicores, e.g. "1000m" = 1 CPU core
    min_gpu_limit=0              # set min_gpu_limit=1 to enable GPU
)
def main(options: Namespace, inputdir: Path, outputdir: Path):
    """
    *ChRIS* plugins usually have two positional arguments: an **input directory** containing
    input files and an **output directory** where to write output files. Command-line arguments
    are passed to this main method implicitly when ``main()`` is called below without parameters.

    :param options: non-positional arguments parsed by the parser given to @chris_plugin
    :param inputdir: directory containing (read-only) input files
    :param outputdir: directory where to write output files
    """

    LOG(DISPLAY_TITLE)
    log_file = outputdir / 'terminal.log'
    logger.add(log_file)
    LOG(f"Logs are stored in {log_file}")

    mapper = PathMapper.file_mapper(inputdir, outputdir, glob=options.inputJSONfile)
    for input_file, output_file in mapper:
        if options.copyInputFile:
            output_file.write_text(input_file.read_text())
        # Open and read the JSON file
        with open(input_file, 'r') as file:
            data = json.load(file)
            retrieve_series(options, data)

def retrieve_series(options, series):
    """Identify individual series and request C-MOVE per series"""
    logger.info(f"=== Series found {len(series)} ===")
    for i, ds in enumerate(series, 1):
        logger.info(
            f"  [{i:03d}]  PatientID={ds.get('PatientID', 'N/A'):<20s}  "
            f"PatientName={str(ds.get('PatientName', 'N/A')):<30s}  "
            f"Date={ds.get('StudyDate', 'N/A'):<10s}  "
            f"UID={ds.get('SeriesInstanceUID', 'N/A')}"
        )

    # C-MOVE per series
    ok = failed = 0
    for ds in series:
        sr_uid = ds.get("SeriesInstanceUID", None)
        st_uid = ds.get("StudyInstanceUID", None)
        if not st_uid:
            logger.warning("Study dataset has no StudyInstanceUID; skipping.")
            failed += 1
            continue
        if cmove_series(options, st_uid, sr_uid):
            ok += 1
        else:
            failed += 1

    logger.info(f"C-MOVE done — success: {ok}  failed: {failed}")

# ---------------------------------------------------------------------------
# C-MOVE
# ---------------------------------------------------------------------------
def cmove_series(args, study_uid: str, series_uid: str) -> bool:
    """Send a C-MOVE request for a single SeriesInstanceUID."""
    ae = AE(ae_title=args.dst_aet)

    move_model = (
        StudyRootQueryRetrieveInformationModelMove
        if args.query_model == "study"
        else PatientRootQueryRetrieveInformationModelMove
    )
    ae.add_requested_context(move_model)

    identifier = Dataset()
    identifier.QueryRetrieveLevel = "SERIES"
    identifier.StudyInstanceUID   = study_uid
    identifier.SeriesInstanceUID   = series_uid

    logger.info(f"C-MOVE series {series_uid} → dest AET '{args.dst_aet}'")

    assoc = ae.associate(args.src_ip, args.src_port, ae_title=args.src_aet)
    if not assoc.is_established:
        logger.error(f"C-MOVE association failed for series {series_uid}")
        return False

    success = False
    try:
        responses = assoc.send_c_move(identifier, args.dst_aet, move_model)
        for status, _ in responses:
            if status:
                code = status.Status
                if code == 0x0000:
                    logger.info(f"C-MOVE success for series {series_uid}")
                    success = True
                elif code in (0xFF00, 0xFF01):
                    pass  # Sub-operations pending
                else:
                    logger.warning(
                        f"C-MOVE status {code} for series {series_uid}"
                    )
    finally:
        assoc.release()

    return success

if __name__ == '__main__':
    main()
