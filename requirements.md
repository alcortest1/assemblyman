# Workflow Copilot Work Trial

## Context

Alcor is building an AI workflow copilot for people performing physical, multi-step procedures.

The long-term product should understand what a person is doing, track progress, detect mistakes, answer questions, and provide useful guidance without becoming confused about the type of interaction taking place.

Enterprise customers generally cannot provide realistic proprietary data before seeing a credible demonstration. For this exercise, you will therefore start without:

- A prepared procedure
- A labeled dataset
- A working baseline
- A predefined evaluation framework

Part of the assignment is deciding how to create an appropriate procedure, dataset, labeling process, evaluation environment, and technical approach.

This is an interview exercise and product prototype. It is not expected to be production-ready or deployed to customers.

## Objective

Build and evaluate a credible prototype of a workflow copilot.

There are many possible features and architectures. The goal is not to build all of them. The goal is to reach a convincing working demonstration while making deliberate, evidence-based decisions about scope and complexity.

You own the sequencing and prioritization of the work.

## Core product outcomes

The workflow copilot should ultimately demonstrate:

1. Tracking progress through a procedure containing multiple steps
2. Detecting an error, omission, or incorrect action
3. Distinguishing between `complete`, `incomplete`, and `uncertain`
4. Returning visual evidence supporting its judgment
5. Answering user questions using the procedure and current-step context
6. Handling tracking, verification, and questions without confusing the different interaction modes

You may select an accessible physical procedure that allows these behaviors to be demonstrated. The procedure should be sufficiently controlled to evaluate while remaining realistic enough to expose meaningful failure modes.

At least one evaluation case should depend on reading or using small visual text—for example, confirming that a cable is connected to the port identified by a nearby label.

## Product progression

We expect the system to progress from simpler interactions toward more complex behavior. Those should be mapped out by first designing the ideal system with most flexible functionalities, identifying each technical risk, and branching out a new interface with that technical risk removed. You should decide how and when to make that progression based on what you learn.

Potential interfaces include:

### Photo-based analysis

The user indicates the current step and provides a photo. The system determines whether the step is complete, incomplete, or uncertain and returns supporting evidence.

### Triggered video or near-real-time analysis

The user can explicitly initiate one of two interactions:

- Ask the system to analyze or verify the current action
- Ask a question about the procedure or current context

At this stage, the system does not need to decide independently when to intervene. Both interactions may be explicitly triggered, including through a voice command or trigger phrase.

### Proactive and reactive operation

The system tracks progress and detects relevant mistakes without being explicitly asked, while continuing to answer questions correctly.

An offline, full-recording analysis may also be useful as an intermediate implementation, evaluation tool, or comparison point. It is an option rather than the principal objective.

Potential extensions include OCR, tool calling, retrieval from procedure documentation, adaptive frame selection, an edge/server architecture, improved observability, or other functionality you believe materially improves the demonstration.

These are possibilities rather than a required feature list. You should prioritize based on the evidence available and the time required.

## Data and evaluation

You should design and implement an initial process for:

- Selecting and representing the procedure
- Capturing useful examples
- Defining ground truth
- Labeling photos or video
- Representing correct, incorrect, and genuinely uncertain cases
- Replaying tests consistently
- Measuring system performance
- Comparing iterations
- Identifying and diagnosing failure modes

You may use any tools or techniques you consider appropriate. Document the process sufficiently that another engineer could understand and reproduce it.

The initial dataset does not need to be large. It should be large and varied enough to support a meaningful initial evaluation, and you should explain the reasoning behind its size and composition.

You should decide which metrics matter. Where metrics conflict—for example, catching more critical errors versus producing too many false alerts—explain the tradeoff you are making.

## Planning and decision-making

Maintain a concise record of:

- The user outcome you are targeting
- The procedure you selected and why
- The interaction model
- The general architecture
- The data-collection and labeling approach
- The evaluation design and metrics
- The largest technical and product risks
- Important assumptions
- What you are deliberately not building
- Results that caused you to change direction
- The reasoning behind major prioritization decisions

This does not need to become extensive documentation. It should make the evolution of the project and its important decisions understandable.

Additional operating constraints may be introduced during the exercise.

## Final review

At the final review, please be prepared to provide:

- A live demonstration
- The procedure and dataset you created
- The labeling and evaluation methodology
- Quantitative and qualitative results
- Examples of correct, incorrect, and uncertain behavior
- The system architecture and important design decisions
- The most significant failures and unresolved risks
- What you changed your mind about
- What you chose not to build and why
- What you would build, remove, or investigate over the following two weeks

A smaller system supported by credible evidence is preferable to a larger system whose behavior cannot be evaluated reliably.
