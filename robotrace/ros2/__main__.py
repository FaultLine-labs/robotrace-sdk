"""Entry point: python -m robotrace.ros2 --config bridge.yaml"""
import sys
from .bridge import main

main(sys.argv[1:])
