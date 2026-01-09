#!/bin/bash

ON_TIME="08:30:00"
OFF_TIME="18:30:00"

cd "`dirname $0`"

sleep_until() {
	# Define your target time
	# Example: Sleep until 5 PM today
	TARGET_TIME="$1" 
	
	# Get the current epoch timestamp (seconds since Jan 1, 1970)
	CURRENT_EPOCH=$(date +%s)
	
	# Get the target epoch timestamp
	# The -d option allows specifying a date/time string
	TARGET_EPOCH=$(date -d "$TARGET_TIME" +%s)
	
	# Calculate the number of seconds to sleep
	SLEEP_SECONDS=$(( TARGET_EPOCH - CURRENT_EPOCH ))
	
	# Check if the target time is in the past
	while [ "$SLEEP_SECONDS" -lt 0 ]; do
		SLEEP_SECONDS=$[SLEEP_SECONDS+86400]
	done
	
	echo "Sleeping for $SLEEP_SECONDS seconds until $TARGET_TIME..."
	sleep "$SLEEP_SECONDS"
	
	echo "Awake! It's now $(date)"
}

not_work_hour() {
	CURRENT_EPOCH=$(date +%s)
	ON_EPOCH=$(date -d $ON_TIME +%s)
	OFF_EPOCH=$(date -d $OFF_TIME +%s)
	if [ $CURRENT_EPOCH -gt $ON_EPOCH ] && [ $CURRENT_EPOCH -lt $OFF_EPOCH ]; then
		return 1
	fi
	return 0
}

start() {
	./tv-control.py 3 'https://b2b.mindline.sg/ddr/'
	./tv-control.py 2 'https://b2b.mindline.sg/ddr2/'
	./tv-control.py 1 'https://b2b.mindline.sg/ddr/'
}

stop() {
	./tv-control.py 3 off
	./tv-control.py 2 off
	./tv-control.py 1 off
}

if [ $# -ge 1 ]; then
	$1
	exit
fi


ini=yes
while :; do
	if [ $ini == no ] || not_work_hour; then
		sleep_until $ON_TIME
		dow=$(date +%u)
		if [[ 12345 == *$dow* ]]; then
			start
		fi
		ini=no
	fi
	sleep_until $OFF_TIME
	stop
done
