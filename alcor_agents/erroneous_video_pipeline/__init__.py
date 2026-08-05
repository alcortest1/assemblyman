"""Controlled erroneous-video generation for the AIM maintenance grading pilot.

Produces synthetic negative examples: for a source video of a task performed
correctly, one regenerated variant in which exactly one rubric-violating mistake
is introduced and everything else — scene, camera, tools, technician, lighting,
timing outside the edit window — is preserved.

Every clip this package writes is synthetic and deliberately wrong. It is
labelled FAIL, marked `synthetic: true` in the manifest, and must never be
presented as footage of real student work.
"""

__all__ = ["analysis", "config", "discovery", "generation", "hosting", "media",
           "models", "outputs", "pipeline", "planning", "prompts", "qa"]
__version__ = "1.0.0"
