"""Workflows: what a task makes (highlights, series, topic, recap, trailer).

Highlights is the original pipeline. Every other workflow plans its clips here
from a sentence-indexed transcript and hands locked segments to the shared
renderer. ``contracts`` holds the types every part codes against; the planner
entry point is ``pipeline.plan_workflow``.
"""
