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

FF_IMAGE='linuxserver/ffmpeg'
client = docker.from_env()
api_client = docker.APIClient(base_url='unix://var/run/docker.sock')


def remove(filename):
  print(f"Removing file: {infile}")
  subprocess.check_call(['sudo', 'rm', '-v', filename])


def file_size_check(filename):
  if os.path.exists(filename):
    size = int(os.path.getsize(filename))
    if size <= 4096:
      print(f"Removing {size} byte file: {filename}")
      remove(filename)
      return False
    return True
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


def get_container_name(task):
  letters = string.ascii_uppercase + string.ascii_uppercase
  suffix = ''.join(random.choice(letters) for i in range(16))
  return f'{task}_{suffix}'


def run_convert(infile, outfile, stream_mapi, in_codec_name):
  (filedir, filename) = get_filepaths(infile)
  print(f"Converting: {infile} -> {outfile}")
  maps = []
  for stream_id in stream_map.keys():
    maps.extend(['-map', f'0:{stream_id}'])
  name=get_container_name('ffmpeg'),
  try:
    container = client.containers.run(
      image=FF_IMAGE,
      detach=True,
      runtime='nvidia',
      devices=['/dev/dri:/dev/dri'],
      environment=['PUID=1001', 'PGID=1001', 'AV_LOG_FORCE_NOCOLOR=1'],
      volumes=[f'{filedir}:{filedir}'],
      name=name,
      entrypoint='/usr/local/bin/ffmpeg',
      command=['-hide_banner', '-analyzeduration', '10M', '-probesize', '10M', '-vsync', '0',
        '-fflags', '+igndts',
        '-hwaccel', 'nvdec', '-c:v', in_codec_name, '-i', f'{filedir}/{infile}', '-max_muxing_queue_size', '2048',
      ] + maps + [ '-crf', '10', '-c:a', 'copy', '-c:s', 'copy', '-c:v', 'hevc_nvenc', '-y', f'{filedir}/{outfile}' ]
      #'-hwaccel', 'nvdec', '-i', f'{filedir}/{infile}', '-max_muxing_queue_size', '2048',
      #'-hwaccel', 'nvdec', '-c:v', in_codec_name, '-i', f'{filedir}/{infile}', '-max_muxing_queue_size', '2048',
    )
    for line in container.logs(stream=True, follow=True):
      text = line.decode("utf-8")
      if 'speed' in text:
        print(f"{text}", end="\r", flush=True)
  except KeyboardInterrupt:
    api_client.stop(container.id)
    api_client.remove_container(container.id)


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
  container = client.containers.run(
    image=FF_IMAGE,
    remove=True,
    devices=['/dev/dri:/dev/dri'],
    environment=['PUID=1001', 'PGID=1001', 'AV_LOG_FORCE_NOCOLOR=1'],
    volumes=[f'{filedir}:{filedir}'],
    name=get_container_name('ffprobe_format'),
    entrypoint='/usr/local/bin/ffprobe',
    command=[ '-v', 'quiet', '-print_format', 'json', '-show_format', '-i', f'{filename}' ]
    )
  out = json.loads(container)
  if 'format' in out:
    if 'duration' in out['format']:
      duration = out['format']['duration']
      return float(duration)
  return False


def get_stream_map(filename):
  stream_map = {}
  codec_name = ''
  (filedir, filename) = get_filepaths(filename)
  print(f"Getting stream maps: {filename}")
  try:
      container = client.containers.run(
        image=FF_IMAGE,
        remove=True,
        devices=['/dev/dri:/dev/dri'],
        environment=['PUID=1001', 'PGID=1001', 'AV_LOG_FORCE_NOCOLOR=1'],
        volumes=[f'{filedir}:{filedir}'],
        name=get_container_name('ffprobe_streams'),
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
        codec_name = stream['codec_name']
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


def get_pct(line, duration):
  if line.startswith('frame=') and 'fps=' in line:
    counted_frames = int(line.split('frame=')[-1].split('fps=')[0].strip())
    if duration:
        progress = counted_frames / 24
        return float(progress) / duration
  return 0


GREEN_FG = '\x1b[38;5;46m'
RED_FG   = '\x1b[38;5;196m'
WHITE_FG = '\x1b[38;5;15m'
BLUE_BG  = '\x1b[48;5;21m'
GRAY_BG  = '\x1b[48;5;237m'
RESET    = '\x1b[0m'


if __name__ == "__main__":
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
  container = run_convert(infile, outfile, stream_map, in_codec_name)
  file_dur_check(infile, outfile)
  end = time.mktime(time.localtime())
  print(f"Done: Elapsed time: %s" % str(datetime.timedelta(seconds=end-start)))
