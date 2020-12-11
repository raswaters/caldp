"""This module creates preview images from reprocessing output data."""

import argparse
import os
import subprocess
import logging
import json
import glob
import shutil
import boto3
from astropy.io import fits

from . import log

# -----------------------------------------------------------------------------------------------------------------

LOGGER = logging.getLogger(__name__)

AUTOSCALE = 99.5

OUTPUT_FORMATS = [("_thumb", 128), ("", -1)]


# -----------------------------------------------------------------------------------------------------------------

def generate_image_preview(input_path, output_path, size):
    cmd = [
        "fitscut",
        "--all",
        "--jpg",
        f"--autoscale={AUTOSCALE}",
        "--asinh-scale",
        f"--output-size={size}",
        "--badpix",
        input_path,
    ]

    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout, stderr = process.communicate()

    if process.returncode > 0:
        LOGGER.error("fitscut failed for %s with status %s: %s", input_path, process.returncode, stderr)
        raise RuntimeError()

    with open(output_path, "wb") as f:
        f.write(stdout)
    return stdout


def generate_image_previews(input_path, preview_dir, filename_base):
    output_paths = []
    for suffix, size in OUTPUT_FORMATS:
        output_path = os.path.join(preview_dir, f"{filename_base}{suffix}.jpg")
        try:
            generate_image_preview(input_path, output_path, size)
        except Exception:
            log.info("Preview file (imaging) not generated for", input_path, "with size", size)
        else:
            output_paths.append(output_path)
    return output_paths


def generate_spectral_previews(input_path, preview_dir):
    cmd = ["make_hst_spec_previews", "-v", "-t", "png", "fits", "-o", preview_dir, input_path]
    err = subprocess.call(cmd)
    if err:
        LOGGER.exception(f"Preview file not generated for {input_path}")
        return []
    else:
        previews = os.listdir(preview_dir)
        return previews


def generate_previews(input_path, preview_dir, filename_base):
    with fits.open(input_path) as hdul:
        naxis = hdul[1].header["NAXIS"]
        ext = hdul[1].header["XTENSION"]
        extname = hdul[1].header["EXTNAME"].strip()
        try:
            instr_char = hdul[1].header["INSTRUME"].strip()[0]
        except Exception:
            instr_char = filename_base[0]
        instr_char = instr_char.lower()

    if naxis == 2 and ext == "BINTABLE" and extname != "ASN":
        print("Generating spectral previews...")
        return generate_spectral_previews(input_path, preview_dir)
    elif naxis >= 2 and ext == "IMAGE" and instr_char not in ["l", "o"]:
        print("Generating image previews...")
        return generate_image_previews(input_path, preview_dir, filename_base)
    else:
        log.warning("Unable to determine FITS file type")
        return []

def get_inputs(ipppssoot, data_dir):
    search_fits = f"{data_dir}/{ipppssoot.lower()[0:5]}*.fits"
    inputs = glob.glob(search_fits)
    return list(sorted(inputs))

def get_previews(ipppssoot, preview_dir):
    png_search = f"{preview_dir}/*.png"
    jpg_search = f"{preview_dir}/*.jpg"
    preview_files = glob.glob(png_search)
    preview_files.extend(glob.glob(jpg_search))
    return list(sorted(preview_files))

def upload_previews(previews, output_uri_prefix):
    """Given `previews` list to upload, copy it to `output_uri_prefix`.
    previews : List of local preview filepaths to upload
       ['./odfa01030/previews/x1d_thumb.png','./odfa01030/previews/x1d.png' ]
    output_uri_prefix : Full path to object to upload including the bucket prefix
        s3://hstdp-batch-outputs/data/stis/odfa01030/previews/
    """        
    client = boto3.client("s3")
    splits = output_uri_prefix[5:].split("/")
    bucket, path = splits[0], "/".join(splits[1:])
    for preview in previews:
        file = os.path.basename(preview)   
        objectname = path+"/"+file
        log.info("Uploading", preview, "to", output_uri_prefix)
        with open(preview, "rb") as f:
            client.upload_fileobj(f, bucket, objectname)

def create_previews_local(input_uri_prefix, output_uri_prefix):
    indir = os.path.abspath(input_uri_prefix.split(":")[-1]) or "."
    input_paths = glob.glob(indir + "/*.fits")
    log.info("Processing", len(input_paths), "FITS files from prefix", input_uri_prefix)
    for input_path in input_paths:
        log.info("Generating previews for", input_path)
        outbase, filename = os.path.split(input_path)
        filename_base, _ = os.path.splitext(filename)
        output_paths = generate_previews(input_path, outbase, filename_base)
        log.info("Generated", len(output_paths), "output files")
        for output_path in output_paths:
            output_uri = os.path.join(output_uri_prefix, os.path.basename(output_path))
            log.info(f"Copying {output_path} to {output_uri}")
            os.makedirs(os.path.dirname(output_uri), exist_ok=True)
            try: 
                shutil.copy(output_path, output_uri)
            except shutil.SameFileError:
                pass

def create_previews_s3(input_uri_prefix, output_uri_prefix, ipppssoot):
    """Generates previews based on s3 downloads
    Returns a list of file paths
    """
    base_path = os.getcwd()
    data_dir = os.path.join(base_path, ipppssoot)
    preview_dir = os.path.join(data_dir, "previews") 
    input_paths = get_inputs(ipppssoot, data_dir)
    log.info("Processing", len(input_paths), "FITS files from ", data_dir)
    # Generate previews to local preview folder inside ipppssoot folder
    for input_path in input_paths:
        log.info("Generating previews for", input_path)
        filename_base = os.path.basename(input_path).split('.')[0]
        generate_previews(input_path, preview_dir, filename_base)
    # list of full paths to preview files
    previews = get_previews(ipppssoot, preview_dir) 
    log.info("Generated", len(previews), "preview files")
    # Upload previews to s3
    upload_previews(previews, output_uri_prefix)


def main(input_uri_prefix, output_uri_prefix, ipppssoot):
    """Generates previews based on input and output directories
    according to specified args
    """
    if input_uri_prefix.startswith("s3://"):
        create_previews_s3(input_uri_prefix, output_uri_prefix, ipppssoot)
    else:
        create_previews_local(input_uri_prefix, output_uri_prefix)

def parse_args():
    parser = argparse.ArgumentParser(description="Create image and spectral previews")
    parser.add_argument("input_uri_prefix", help="s3 or local directory containing FITS images")
    parser.add_argument("output_uri_prefix", help="S3 URI prefix for writing previews")
    parser.add_argument("ipppssoot", help="IPPPSSOOT for instrument data")
    return parser.parse_args()

def cmdline():
    args = parse_args()
    main(args.input_uri_prefix, args.output_uri_prefix, args.ipppssoot)

if __name__ == "__main__":
    cmdline()
