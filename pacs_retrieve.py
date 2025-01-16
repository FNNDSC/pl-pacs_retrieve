#!/usr/bin/env python

from pathlib import Path
from argparse import ArgumentParser, Namespace, ArgumentDefaultsHelpFormatter
from pflog import pflog
from loguru import logger
from chris_plugin import chris_plugin, PathMapper
import pfdcm
import json
import sys
import pprint
import os

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
__version__ = '1.0.4'

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


parser = ArgumentParser(description='A plugin to retrieve DICOM images from a remote PACS using pfdcm',
                        formatter_class=ArgumentDefaultsHelpFormatter)

parser.add_argument(
    '--PACSurl',
    default='',
    type=str,
    help='endpoint URL of pfdcm'
)
parser.add_argument(
    '--PACSname',
    default='MINICHRISORTHANC',
    type=str,
    help='name of the PACS'
)
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
    '--retrieveStudy',
    default=False,
    action="store_true",
    help='If specified, retrieve the entire study'
)
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

    mapper = PathMapper.file_mapper(inputdir, outputdir, glob=options.inputJSONfile)
    for input_file, output_file in mapper:
        if options.copyInputFile:
            output_file.write_text(input_file.read_text())
        # Open and read the JSON file
        with open(input_file, 'r') as file:
            data = json.load(file)
            directive = {}
            op_json_file_path = ""
            if options.retrieveStudy:
                directive["AccessionNumber"] = data[0]["AccessionNumber"]
                retrieve_response = pfdcm.retrieve_pacsfiles(directive, options.PACSurl, options.PACSname)

                LOG(f"response: {pprint.pformat(retrieve_response)}")
                op_json_file_path = os.path.join(options.outputdir, f"{data[0]["AccessionNumber"]}_retrieve.json")
            else:

                for series in data:
                    directive["SeriesInstanceUID"] = series["SeriesInstanceUID"]
                    directive["StudyInstanceUID"] = series["StudyInstanceUID"]
                    retrieve_response = pfdcm.retrieve_pacsfiles(directive, options.PACSurl, options.PACSname)

                    LOG(f"response: {pprint.pformat(retrieve_response)}")
                    op_json_file_path = os.path.join(options.outputdir, f"{series["SeriesInstanceUID"]}_retrieve.json")
            # Open a json writer, and use the json.dumps()
            # function to dump data
            with open(op_json_file_path, 'w', encoding='utf-8') as jsonf:
                jsonf.write(json.dumps(retrieve_response, indent=4))


if __name__ == '__main__':
    main()
