# Feature Specification: Align Spec Kit Templates with the Adopted Constitution

**Feature Branch**: `001-align-speckit-governance`

**Created**: 2026-10-04

**Status**: Draft

**Input**: User description: "`.specify/templates/plan-template.md` 的 Constitution Check 须列 Principles I–X，并用 Complexity Tracking 记录违规；`.specify/templates/tasks-template.md` 的 tasks 须按 vertical slice／user story 分组；把 `.specify/` 纳入版控"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Plan Author Is Forced Through the Constitution Check (Priority: P1)

A developer or agent drafting a feature plan opens the plan template and is required to
walk every one of the ten adopted principles, state whether the plan complies, and record
any violation with its justification and the simpler alternative that was rejected — before
any design work begins. The check is repeated after design, because a plan that complied
before design can violate a principle after it.

**Why this priority**: The constitution was adopted with a "Constitution Check gate" that
must run before design (Phase 0) and again after design (Phase 1), with violations recorded
in a Complexity Tracking table. Without the template carrying that structure, the gate is
a prose intention no one can comply with mechanically. Every other governance guarantee
depends on this one.

**Independent Test**: Can be fully tested by creating one plan that violates a
non-negotiable principle and confirming the template forces a recorded justification rather
than allowing the plan to proceed silently.

**Acceptance Scenarios**:

1. **Given** a new plan is started, **When** the author reaches the constitution check,
   **Then** all ten principles are listed as separate items to be answered, not summarised
   as a single question.
2. **Given** a plan violates a principle, **When** the author records it, **Then** the
   record states which principle, why it is needed, and which simpler alternative was
   rejected.
3. **Given** a plan violates a non-negotiable principle, **When** no justification is
   recorded, **Then** the plan does not pass the check and cannot advance to tasks.
4. **Given** a plan passed the check before design, **When** design changes the approach,
   **Then** a second check is required against the post-design state.

---

### User Story 2 - Task Author Groups Work by Vertical Slice (Priority: P2)

A developer or agent converting an approved plan into tasks organises the work so that
each group delivers an independently usable, end-to-end slice — one that crosses the
minimum layers needed to be verifiable — rather than splitting work by technical layer
(all persistence work, then all API work, then all UI work).

**Why this priority**: The constitution makes "Vertical Slice First" a non-negotiable
principle and makes a feature "incomplete" if its important behaviour cannot be reliably
verified. Task lists organised by layer produce exactly the speculative-infrastructure,
unverifiable-increment outcome the constitution forbids. This is second priority because it
matters most at the point tasks are written, which is after the plan already passed P1's gate.

**Independent Test**: Can be fully tested by producing tasks for one feature and confirming
that each task group is independently demonstrable and that no group exists purely to
prepare infrastructure for a later group.

**Acceptance Scenarios**:

1. **Given** an approved plan, **When** tasks are generated, **Then** tasks are grouped by
   user story or vertical slice, and each group is labelled with the priority of the story
   it serves.
2. **Given** a task group, **When** it is reviewed, **Then** it can be demonstrated on its
   own without depending on a later group being complete.
3. **Given** a plan with a stated user story ordering, **When** tasks are generated, **Then**
   task groups follow that ordering, so an early group is a viable increment.

---

### User Story 3 - Reviewer Can Trace Governance History (Priority: P3)

A reviewer inspecting the repository can see the constitution and the templates it governs
in the project's version-controlled history, so an amendment to a principle is reviewable,
attributable, and revertible like any other change — and so a fresh checkout on any machine
behaves identically to the machine that created the change.

**Why this priority**: The constitution requires amendments to be deliberate and documented,
and requires compliance review on plans and implementations. A governance document outside
version control has no history, so "deliberate and documented" cannot be enforced. This is
lowest priority because it is a precondition for governance durability rather than for
governance being applied to the current plan.

**Independent Test**: Can be fully tested by amending the constitution on one machine,
fetching on another, and confirming both machines resolve the same constitution and that
the amendment appears as a reviewable change with an identifiable author.

**Acceptance Scenarios**:

1. **Given** a fresh checkout on any supported machine, **When** the specification workflow
   is started, **Then** the same constitution and the same templates are used as on every
   other machine.
2. **Given** an amended constitution, **When** a reviewer inspects the history,
   **Then** the previous version, the new version, and the reason are recoverable from the
   project history rather than from a machine's local files.
3. **Given** machine-local specification state, **When** the shared configuration is added
   to version control, **Then** that local state is excluded.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The plan template MUST require a constitution check covering each of the ten
  principles individually, before design work begins.
- **FR-002**: The plan template MUST require the constitution check to be repeated after
  design is complete, against the post-design state.
- **FR-003**: The plan template MUST provide a Complexity Tracking structure in which a
  violation records the principle violated, why the violation is needed, and the simpler
  alternative that was rejected.
- **FR-004**: The plan template MUST make an unjustified violation block progression to
  task definition.
- **FR-005**: The plan template MUST identify which principles are non-negotiable, since
  those take precedence over the others during conflict resolution.
- **FR-006**: The tasks template MUST group tasks by vertical slice or user story, and MUST
  label each group with the priority of the story it serves.
- **FR-007**: The tasks template MUST require each task group to be independently
  demonstrable, so no group exists solely to prepare infrastructure for a later group.
- **FR-008**: Task groups MUST follow the priority ordering of the user stories in the
  approved plan, so that an earlier group constitutes a viable increment.
- **FR-009**: The specification configuration directory MUST be brought under the project's
  version control.
- **FR-010**: Machine-local specification state MUST remain excluded from version control.
- **FR-011**: Templates and constitution MUST remain resolvable through the existing
  resolution mechanism, so a project override still wins over a bundled default.
- **FR-012**: A future constitution amendment MUST NOT silently invalidate existing plans;
  the plan template MUST record which constitution version the plan was checked against.
- **FR-013**: The tasks template MUST NOT reintroduce layer-ordered groupings (all storage
  work, then all interface work) as an accepted alternative to vertical slices.
- **FR-014**: Neither template change may remove or weaken any guidance already present in
  those templates.

### Key Entities

- **Principle**: A numbered, named rule (I through X) with a normative strength
  (non-negotiable or advisory) and a stated rationale. The constitution is the set of these.
- **Constitution Check**: A structured, per-principle compliance assessment performed at a
  defined point in the planning workflow, with a recorded outcome per principle.
- **Complexity Tracking entry**: A record of one violated principle, why the violation is
  necessary, and which simpler alternative was considered and rejected.
- **Vertical slice**: A unit of planned work that yields an independently usable and
  verifiable increment, crossing only the layers that increment requires.
- **User story**: A prioritised, independently testable user journey that a slice delivers.
- **Governance record**: The versioned history of constitution amendments and the plan-time
  checks performed against them.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of plans created after this change contain an individual compliance
  outcome for all ten principles; no plan proceeds with a single summarised answer.
- **SC-002**: A plan containing a non-negotiable-principle violation without a recorded
  justification is blocked at the check in 100% of cases.
- **SC-003**: A reviewer can name the constitution version any existing plan was checked
  against, for 100% of plans created after this change, without consulting the machine that
  wrote it.
- **SC-004**: In a sample of ten task lists generated after this change, 100% of task groups
  are demonstrable without a later group being complete.
- **SC-005**: A fresh checkout on each of the three supported machines resolves an identical
  constitution and identical templates.
- **SC-006**: A constitution amendment appears in the shared history with an identifiable
  author and a recoverable prior version.
- **SC-007**: No machine-local specification state appears in the shared history.

## Assumptions

- The ten principles and their numbering are those of the constitution ratified 2026-10-04;
  a future amendment that adds, removes, or renumbers a principle will require the plan
  template's principle list to be revisited, which FR-012 makes explicit rather than silent.
- Template resolution continues to honour project overrides ahead of bundled defaults, so
  these changes alter the bundled defaults only.
- "Version control" means the project's existing shared repository used for source code;
  no new hosting or access model is introduced.
- The existing per-machine extension override mechanism continues to work unchanged; it is
  local configuration, not shared configuration.
- No feature specification exists yet in this project, so this is the first entry under the
  specification directory and establishes the numbering sequence for subsequent features.
