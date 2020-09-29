#!/bin/bash

while getopts "adnf:" opt; do
  case ${opt} in
    a ) ATI=TRUE ;;
    d ) DELETE=TRUE ;;
    n ) NVENC=TRUE ;;
    f ) INFILE="${OPTARG}" ; echo $INFILE ;;
    \? ) echo "Usage: cmd [-d] [-n] -f <filename>" ;;
  esac
done
shift $((OPTIND-1))

if [[ -z "${INFILE}" ]] ; then
  echo "Missing argument: File to compress"
  exit 1
fi

FILE="$(basename "${INFILE}")"
DIRNAME="$(dirname "${INFILE}")"
OUTFILE="${FILE%.*}.mp4"

IN_ENCODER=""
OUT_ENCODER="-c:v libx265"

# pick encoder settings: cpu is default, nvidia or ati from opts
[[ -n ${NVENC} ]] && IN_ENCODER="-hwaccel cuvid -c:v h264_cuvid" OUT_ENCODER="-c:v hevc_nvenc"
[[ -n ${ATI} ]] && IN_ENCODER="-hwaccel videotoolbox"

# linux vs mac core count
[[ -d /proc ]] && CORES=$(grep processor /proc/cpuinfo | wc -l) || CORES=$(sysctl -n hw.ncpu)

# max 16 threads
CORES=$(( ${CORES} > 16 ? 16 : ${CORES} ))

check_disk() {
    FREE=$(df "$(pwd)"  | tail -n1 | awk '{print $4}')
    [[ -n "$FREE" ]] || (echo "can't determine free space, stopping" ; exit 1)
    [[ $FREE -ge 10000000000 ]] || (echo "not enough free space, stopping" ; exit 1)
}

check_file() {
    sync
    IN="${@%%----*}"
    OUT="${@##*----}"

    IN_SIZE=$(du "${IN}" | awk '{print $1}')
    IN_DUR=$(ffprobe -i "${IN}" 2>&1 | grep Duration | awk '{print $2}' | awk -F '.' '{print $1}')

    sync ; sleep 3
    OUT_SIZE=$(du "${OUT}" | awk '{print $1}')
    OUT_DUR=$(ffprobe -i "${OUT}" 2>&1 | grep Duration | awk '{print $2}' | awk -F '.' '{print $1}')

    echo $IN_SIZE $OUT_SIZE
    echo $IN_DUR  $OUT_DUR

    [[ -z "${OUT_DUR}" ]] && return 1  # no duration
    (( ${OUT_SIZE} <= 100000 )) && return 1   # too small
    [[ "${IN_DUR}" == "${OUT_DUR}" ]] && return 0  # winner winner
}

#### action time
#check_disk
pushd "${DIRNAME}"
echo "Compressing ${FILE} now ..."
set -x
ffmpeg -y -vsync 0 ${IN_ENCODER} -i "${FILE}" -c:a copy -crf 26 -preset medium -threads ${CORES} ${OUT_ENCODER} "${OUTFILE}" && \
    [[ -n "${DELETE}"  ]] && \
        ( check_file "${FILE}----${OUTFILE}" && ( echo "removing ${FILE} now" ; rm -v "${FILE}" ))
set +x
popd
