#!/bin/bash
set -ex

echo "Running create-topics.sh"

BOOTSTRAP_SERVER=${KAFKA_BOOTSTRAP_SERVERS:-localhost:9092}

TOPICS=(
  "tmfiling-preassessment-events"
  "tmeasyfiling-preassessment-events"
  "tmprecheck-preassessment-events"
  "_schemas"
)

for topic in "${TOPICS[@]}"
do
  echo "Creating topic: $topic"
  /usr/bin/kafka-topics --bootstrap-server "$BOOTSTRAP_SERVER" --create --if-not-exists --topic "$topic" --partitions 1 --replication-factor 1 || true
done

echo "All topics created (or already exist)."
