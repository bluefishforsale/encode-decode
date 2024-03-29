#!/usr/bin/python3

import datetime
import docker
import json
import os
import random
import re
import string
import subprocess
import sys
import time
from pprint import pprint

# FF_IMAGE='linuxserver/ffmpeg'
FF_IMAGE='jrottenberg/ffmpeg'
FF_TAG='4.1-nvidia'

client = docker.from_env()
api_client = docker.APIClient(base_url='unix://var/run/docker.sock')


def remove(filename):
  print(f"Removing file: {filename}")
  subprocess.check_call(['sudo', 'rm', '-v', filename])


def file_size_check(filename):
  if not os.path.exists(filename):
    return False
  size = int(os.path.getsize(filename))
  if size >= 40960:
    return True
  print(f"File: {filename} is {size} byte(s)" )
  remove(filename)
  return False


def file_dur_check(infile, outfile):
  #print(f"Checking if {infile} exists")
  in_dur = get_duration(infile)

  #print(f"Checking if {outfile} exists")
  if not os.path.exists(outfile):
    return False
  out_dur = get_duration(outfile)

  #print(f"{in_dur} - {out_dur}")
  if (in_dur and out_dur):
    delta_dur = abs(in_dur - out_dur)
    if delta_dur < 60:
      remove(infile)
      return True


def get_container_name(task, infile):
  infile_clean = re.sub(r'[^a-zA-Z0-9]', '', infile)
  if len(infile_clean) >= 11:
      infile_trunc = infile_clean[:13]
  else:
      infile_trunc = infile_clean

  letters = string.ascii_uppercase + string.ascii_uppercase
  suffix = ''.join(random.choice(letters) for i in range(4))
  return f'{infile_trunc}_{task}_{suffix}'


def run_convert(infile, outfile, stream_map, in_codec_name):
  (filedir, filename) = get_filepaths(infile)
  print(f"Converting: {infile} -> {outfile}")
  maps = []
  for stream_id in stream_map.keys():
    maps.extend(['-map', f'0:{stream_id}'])
  name=get_container_name('ffmpeg', infile),
  try:
    container = client.containers.run(
      image=f'{FF_IMAGE}:{FF_TAG}',
      detach=True,
      runtime='nvidia',
      devices=['/dev/dri:/dev/dri'],
      environment=['PUID=1001', 'PGID=1001', 'AV_LOG_FORCE_NOCOLOR=1'],
      volumes=[f'{filedir}:{filedir}'],
      name=name,
      entrypoint='/usr/local/bin/ffmpeg',
      command=[
        '-hide_banner', '-analyzeduration', '10M', '-probesize', '10M', '-vsync', '0',
        '-fflags', '+igndts', '-flags', '+global_header', '-hwaccel', 'nvdec', '-c:v', in_codec_name,
        '-hwaccel_output_format', 'cuda', '-i', f'{filedir}/{infile}', '-max_muxing_queue_size', '2048',
        '-crf', '1', '-c:a', 'copy', '-c:s', 'copy', '-c:v', 'hevc_nvenc',  '-y', f'{filedir}/{outfile}'
      ]
    )
    for line in container.logs(stream=True, follow=True):
      text = line.decode("utf-8")
      if 'speed' in text:
        print(f"{text}", end="\r", flush=True)
  except KeyboardInterrupt:
    api_client.stop(container.id)
    api_client.remove_container(container.id)
  except Exception as e:
    #barf stderr logs on fail
    print(container.logs(stdout=True, stderr=True))
    api_client.stop(container.id)
    api_client.remove_container(container.id)
    pprint(e)
    sys.exit(1)


def get_filepaths(filename):
  if filename.startswith('/'):
    filedir = '/'.join(filename.split('/')[0:-1])
  else:
    filedir = os.getcwd()
    filename = filedir + '/' + filename
  return (filedir, filename)


def get_duration(filename):
  duration = 0
  (filedir, filename) = get_filepaths(filename)
  esc_infile = re.escape(filename)
  if not os.path.exists(filename):
    return False
  try:
    container = client.containers.run(
      image=f'{FF_IMAGE}:{FF_TAG}',
      remove=True,
      devices=['/dev/dri:/dev/dri'],
      environment=['PUID=1001', 'PGID=1001', 'AV_LOG_FORCE_NOCOLOR=1'],
      volumes=[f'{filedir}:{filedir}'],
      name=get_container_name('ffprobe_format', filename),
      entrypoint='/usr/local/bin/ffprobe',
      command=[ '-v', 'quiet', '-print_format', 'json', '-show_format', '-i', f'{filename}' ]
      )
    out = json.loads(container)
    if 'format' in out:
      if 'duration' in out['format']:
        duration = out['format']['duration']
        return float(duration)
  except Exception as e:
    pprint(e)
    sys.exit(1)
  return False


def get_stream_map(filename):
  stream_map = {}
  codec_name = ''
  (filedir, filename) = get_filepaths(filename)
  print(f"Getting stream maps: {filename}")
  try:
      container = client.containers.run(
        image=f'{FF_IMAGE}:{FF_TAG}',
        remove=True,
        devices=['/dev/dri:/dev/dri'],
        environment=['PUID=1001', 'PGID=1001', 'AV_LOG_FORCE_NOCOLOR=1'],
        volumes=[f'{filedir}:{filedir}'],
        name=get_container_name('ffprobe_streams', filename),
        entrypoint='/usr/local/bin/ffprobe',
        command=[ '-v', 'quiet', '-print_format', 'json', '-show_streams', '-i', f'{filename}' ]
        )
      streams = json.loads(container)['streams']
      #pprint(streams)
  except Exception as e:
    pprint(e)
    sys.exit(1)
  for num, stream in enumerate(streams):
    if stream['codec_type'] in ['audio', 'subtitle', 'video']:
      if stream['codec_type'] == 'video':
        # only store the first video codec: todo this is lazy
        if not codec_name:
          # store any codec name we find
          codec_name = stream['codec_name']
          # oh, but if it's h264, then rewrite it to be nvidia encoder
          if codec_name == 'h264':
              codec_name = f"{codec_name}_cuvid"
      if stream['codec_type'] not in stream_map.values():
        if not stream.get('disposition', {'default': 0}).get('default', 0):
          stream_map[num] = stream['codec_type']
        elif stream.get('tags', {'language': ''}).get('language', 'eng') == 'eng':
          stream_map[num] = stream['codec_type']
        else:
          stream_map[num] = stream['codec_type']
  print('Stream mapping: %s' %
        ', '.join([f'{k}: {v}' for k, v in sorted(stream_map.items())]))
  if any([True for x in ['audio', 'video'] if x not in stream_map.values()]):
    sys.exit(1)
  return stream_map, codec_name


def get_outfile(infile):
  outdirs = infile.split('/')[:-1]
  infile = infile.split('/')[-1]
  outfile_split = []
  infile_split = infile.split('.')[:-1]
  infile_split = '_'.join('_'.join(infile_split).split('-')).split('_')
  for x in infile_split:
    if '265' in x or 'HEVC' in x:
      sys.exit(0)
    if '264' in x or 'xvid' in x.lower():
      outfile_split.append('HEVC')
    else:
      outfile_split.append(x)
  if 'HEVC' not in outfile_split:
    outfile_split.append('HEVC')
  outfile = '-'.join(outfile_split)
  outfile = '.'.join([outfile] + ['mkv'])
  if outfile == infile:
    outfile_split = outfile.split('.')
    outfile = '.'.join(outfile[:-1] + ['new', outfile[-1]])
  if outdirs:
    outfile = '/'.join(outdirs + [outfile])
  return outfile


################################################################################
if __name__ == "__main__":

  ## uncomment these sections to cleanup images and pull freshies
  ## remove all old images TODO: only do this once a day
  # images = [ x.id for x in client.images.list() if 'ffmpeg' in " ".join(x.tags) ]
  # [ client.images.remove(image=x, force=True) for x in [ x.id for x in client.images.list() if 'ffmpeg' in " ".join(x.tags) ] ]

  ## pull a fresh image
  client.images.pull(FF_IMAGE, tag=FF_TAG)

  start = time.mktime(time.localtime())
  infile = ' '.join(sys.argv[1:])
  outfile = get_outfile(infile)
  if not file_size_check(infile):
    print(f"Skipping {infile}: empty or non-existent")
    sys.exit(1)
  if file_size_check(outfile):
    if file_dur_check(infile, outfile):
      sys.exit(0)
  stream_map, in_codec_name  = get_stream_map(infile)
  if run_convert(infile, outfile, stream_map, in_codec_name):
      file_dur_check(infile, outfile)
      os.chown(outfile, 1001, 1001)
  end = time.mktime(time.localtime())
  print(f"Done: Elapsed time: %s" % str(datetime.timedelta(seconds=end-start)))
  client.containers.prune()
