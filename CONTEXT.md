# ContextFlow

ContextFlow turns observable work signals into an explainable, session-isolated understanding of a user's current work situation.

## Language

**Sensed Event**:
An immutable observation about user activity, supplied by a named source at a specific time. It is evidence, not a conclusion about the user.
_Avoid_: User state, trigger

**Source Signal**:
A source-specific observation accepted by an ingress Adapter before it is normalized as a Sensed Event.
_Avoid_: Context State, conclusion

**Context State**:
The current, revisable interpretation of a user's work situation, derived from recent Sensed Events or an explicit trusted update.
_Avoid_: Event, profile

**Opportunity State**:
A Context State describing newly available work, such as a stable dataset ready for optional analysis, rather than claiming what the user is personally doing.
_Avoid_: User activity, user intent

**State Transition**:
A recorded change from one Context State to another, with the Sensed Event and evidence that caused it.
_Avoid_: Event log

**Event Window**:
The recent time interval whose Sensed Events remain eligible to influence the Context State.
_Avoid_: History, memory

**Evidence**:
Human-readable reasons supporting a Context State and its confidence.
_Avoid_: Raw event, explanation text

**Proactive Loop**:
A guarded sequence in which a Sensed Event changes Context State and automatically prepares reviewable work without crossing an external-write confirmation boundary.
_Avoid_: Automation, unattended execution

**Confirmation Boundary**:
The point after safe local preparation and before an action changes an external system, communicates with another person, or makes a consequential local change.
_Avoid_: Approval step, warning

**Event Identity**:
A stable identifier derived from source identity and content, used to guarantee that replaying the same observation does not repeat orchestration.
_Avoid_: Draft ID, filename

**Calendar Observation**:
A read-only observation of a scheduled time window, response status, or calendar change. It proves what the calendar says, not that the user actually attended or performed the scheduled activity.
_Avoid_: Attendance, user activity

**Meeting Window**:
The scheduled interval of an accepted, non-cancelled calendar event. Entering this interval supports a probabilistic work-state interpretation but does not prove attendance.
_Avoid_: Meeting attendance, transcript

**Calendar Baseline**:
The first synchronized view of a calendar source, used to establish known events without treating historical meetings as newly ended events.
_Avoid_: Event history, import
