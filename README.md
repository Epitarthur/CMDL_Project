# CMDL_Project

Plan: Music Player with a Metadata-Quality-Aware Recommender
1. Core idea

The two topics are linked: recommendation quality depends on metadata quality, and recommendations can reveal metadata problems. So the system has three parts:

Music player and catalog: browse, play, search, log listening events.
Metadata Quality Evaluation (MQE) framework: scores every track and flags problems.
Recommender: suggests tracks and uses the quality scores to decide how much to trust each track's metadata.