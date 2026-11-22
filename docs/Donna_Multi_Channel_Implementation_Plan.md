# DONNA --- MULTI-CHANNEL PERSONAL AI ASSISTANT

## Product Scope, System Architecture & Full Implementation Plan

**Project:** Donna\
**Project direction:** Extend existing Donna into a multi-channel
personal AI operating system.\
**Existing repository:** https://github.com/yashthikane/mcps\
**Initial communication channel:** WhatsApp using OpenWA\
**Future communication channels:** Telegram, Slack, Discord and others\
**Primary AI provider:** Groq (existing integration)\
**Backend:** Existing FastAPI application\
**Frontend:** Existing React + Vite dashboard\
**Tool architecture:** Existing MCP Hub and integrations\
**Initial database:** Existing SQLite storage\
**Deployment philosophy:** Local-first, cost-conscious and modular.

------------------------------------------------------------------------

# 1. PROJECT VISION

Donna is a personal AI assistant that can communicate with the user and
authorized contacts across multiple platforms.

Instead of being limited to a website or dashboard, Donna should be
accessible wherever communication happens.

A user should be able to interact with Donna through:

-   Donna's existing web dashboard.
-   WhatsApp.
-   Telegram.
-   Slack.
-   Future supported communication platforms.
-   Future voice interfaces.

All these channels should connect to the same central Donna
intelligence.

A user configures Donna once with their Groq API key, Google account,
Gmail, Google Calendar and other supported integrations. Donna can then
use those same services regardless of whether a request arrives through
WhatsApp, Telegram or the web dashboard.

There should not be separate AI configurations, duplicate agent runtimes
or duplicated integrations for every channel.

**Core principle: Build Donna once. Connect it everywhere.**

# 2. PROBLEM STATEMENT

Traditional AI assistants are often restricted to a single interface.
This creates several problems:

1.  Users must open a particular application to interact with their
    assistant.
2.  Integrations and credentials may need to be configured repeatedly
    across different experiences.
3.  Conversations become fragmented across platforms.
4.  Adding another communication platform often requires rebuilding
    existing functionality.
5.  Different interfaces may have inconsistent permissions and
    capabilities.
6.  Context and memory can become disconnected.
7.  Automated actions can become difficult to control and audit.

Donna should solve these problems by providing one central intelligence
system with multiple communication interfaces.

# 3. PROJECT OBJECTIVES

## 3.1 Centralized intelligence

All communication channels must use the same underlying Donna agent
runtime.

## 3.2 Modular channels

Every communication platform must be implemented as an independent
adapter using a standardized interface.

## 3.3 Shared integrations

Gmail, Google Calendar, Google Meet, Notion, Tasks and Expense Tracker
integrations must be accessible through the same central tool ecosystem.

## 3.4 Contact-specific automation

The user must be able to choose which contacts can interact with Donna
and what Donna can do on their behalf.

## 3.5 Contextual conversations

Donna must maintain appropriate conversation history, personal memory
and contact-specific context.

## 3.6 Controlled autonomy

Donna should be capable of responding automatically, extracting tasks,
checking availability and preparing actions, while respecting configured
permissions and approval requirements.

## 3.7 Future extensibility

Adding Telegram or Slack should not require changes to the central
intelligence architecture.

# 4. PROJECT SCOPE

## 4.1 Included in the project

  Module                  Scope
  ----------------------- --------------------------------------------
  Central Agent Runtime   Shared AI execution engine
  Channel Manager         Register and manage communication channels
  WhatsApp Integration    Receive and send messages using OpenWA
  Telegram Architecture   Future adapter support
  Slack Architecture      Future adapter support
  Contact Management      Allowlist and contact profiles
  Permission Engine       Contact and channel-level permissions
  Conversation Manager    Independent channel conversations
  Memory Manager          Global and contact-specific memory
  MCP Hub                 Reuse existing MCP integrations
  Task Integration        Connect existing Task Manager
  Expense Integration     Connect existing Expense Tracker
  Gmail Integration       Search, summarize and draft emails
  Calendar Integration    Availability and event management
  Google Meet             Meeting links through Calendar integration
  Approval Manager        Approve sensitive actions
  Automation Engine       Reminders and scheduled workflows
  Audit System            Track messages and executed actions
  Donna Dashboard         Manage channels, contacts and policies

## 4.2 Excluded from the initial release

The following should not delay the initial implementation:

-   Fully autonomous financial transactions.
-   Unrestricted messaging to arbitrary contacts.
-   Public multi-tenant SaaS infrastructure.
-   Enterprise Slack administration.
-   Complex multi-agent orchestration.
-   Paid cloud infrastructure.
-   Custom foundation-model hosting.
-   Full voice calling.
-   Autonomous access to every file or account.
-   Automatic execution of irreversible actions.

These may be considered in future versions.

# 5. EXISTING DONNA --- REUSE BEFORE REBUILDING

The existing repository is the source of truth.

The implementation must first audit the existing codebase and reuse
working functionality.

Existing architecture and capabilities identified in the repository
review include:

-   FastAPI backend.
-   React and Vite frontend.
-   Groq-based LLM integration.
-   Tool calling.
-   MCP Hub and MCP server management.
-   Gmail integration.
-   Google Calendar integration.
-   Notion integration.
-   Conversation history.
-   SQLite persistence.
-   Credential storage through Windows Credential Manager.
-   Approval mechanisms for sensitive operations.
-   Server-sent events for web chat responses.

These components should be extended rather than duplicated.

## Reuse policy

  Existing component   Implementation approach
  -------------------- ------------------------------------------------------------
  FastAPI              Extend existing application
  Agent                Refactor shared execution into channel-independent service
  Groq                 Reuse existing LLM configuration
  MCP Hub              Reuse tool discovery and execution
  Gmail                Reuse existing integration
  Calendar             Reuse existing integration
  Notion               Reuse existing integration
  SQLite               Add migrations and tables
  Credential Vault     Reuse for sensitive credentials
  React Dashboard      Extend existing dashboard
  Approval System      Extend for channel-originated actions

**Important:** Before modifying any existing component, inspect its
implementation and tests. Do not assume that a feature is complete
merely because a module or configuration exists.

# 6. HIGH-LEVEL SYSTEM ARCHITECTURE

Donna should have five major architectural layers.

## Layer 1 --- Communication Channels

This layer handles external communication.

Initial channel:

-   WhatsApp through OpenWA.

Future channels:

-   Telegram.
-   Slack.
-   Discord.
-   Other messaging services.
-   Voice interfaces.

Every channel converts platform-specific messages into Donna's
standardized internal message format.

## Layer 2 --- Channel Manager

Responsibilities:

-   Register channels.
-   Connect and disconnect channels.
-   Maintain channel configuration.
-   Handle incoming events.
-   Send outgoing messages.
-   Normalize platform-specific messages.
-   Track channel status.
-   Handle reconnections and errors.
-   Apply channel-level restrictions.

## Layer 3 --- Donna Intelligence

This is the central brain.

It contains:

1.  Agent Runtime.
2.  LLM Gateway.
3.  Intent Router.
4.  Context Manager.
5.  Memory Manager.
6.  Permission Engine.
7.  Response Generator.
8.  Approval Manager.

Every communication channel calls this same intelligence layer.

## Layer 4 --- MCP and Integrations

This layer provides access to existing and future services:

-   Gmail.
-   Google Calendar.
-   Google Meet through Calendar.
-   Notion.
-   Task Manager.
-   Expense Tracker.
-   Other MCP servers.

## Layer 5 --- Storage and Security

Responsibilities:

-   Contact information.
-   Channel accounts.
-   Conversations.
-   Message history.
-   Memory.
-   Permissions.
-   Approval requests.
-   Automation rules.
-   Audit records.
-   Event deduplication.

# 7. ARCHITECTURE FLOW

## Incoming message

1.  A contact sends Donna a WhatsApp message.
2.  OpenWA receives the message.
3.  OpenWA forwards the event to Donna's secured webhook.
4.  Donna validates the event and checks for duplicates.
5.  Donna identifies the channel and sender.
6.  Donna checks whether the sender is authorized.
7.  Donna checks the sender's contact-specific policy.
8.  Donna loads the appropriate conversation context and memory.
9.  The message is passed to the central Agent Runtime.
10. The LLM identifies the user's intent.
11. The agent determines whether tools are required.
12. The Permission Engine checks whether the requested actions are
    permitted.
13. The agent executes permitted tools or creates an approval request.
14. Donna generates the response.
15. The channel adapter sends the response through WhatsApp.
16. Donna stores the message, execution result and relevant audit
    information.

**Important execution rule:** Unauthorized messages must be rejected
before they reach the LLM. A disabled contact must not consume
unnecessary AI tokens or gain access to Donna's tools.

## Outgoing message

The central Agent Runtime should not know whether the response is going
to WhatsApp, Telegram or Slack.

It should return a standardized response object. The Channel Manager
determines how that response is delivered.

# 8. STANDARDIZED CHANNEL ARCHITECTURE

Every communication platform must follow a common channel contract.

## Suggested interface

``` python
class BaseChannel:

    async def connect(self):
        pass

    async def disconnect(self):
        pass

    async def get_status(self):
        pass

    async def receive_event(self, event):
        pass

    async def send_message(self, recipient, message):
        pass

    async def normalize_event(self, event):
        pass

    async def health_check(self):
        pass
```

The actual interface should use appropriate abstract methods, typed
models and error contracts.

## Channel adapters

``` text
donna/
    channels/
        __init__.py
        base.py
        registry.py
        manager.py

        whatsapp/
            __init__.py
            client.py
            webhook.py
            events.py
            normalizer.py
            sender.py

        telegram/
            __init__.py
            client.py
            webhook.py
            normalizer.py
            sender.py

        slack/
            __init__.py
            client.py
            webhook.py
            normalizer.py
            sender.py
```

Only WhatsApp needs to be implemented in the first release. Telegram and
Slack are future extension points, not requirements for the first
release.

## Standard message model

All channel adapters should convert incoming events into a common
internal model.

``` python
class IncomingMessage:
    event_id: str
    channel_id: str
    channel_type: str

    sender_id: str
    sender_name: str | None

    conversation_id: str
    message_id: str

    message_type: str
    text: str | None

    timestamp: datetime
    attachments: list
    metadata: dict
```

The exact schema should be implemented with the project's existing
validation conventions.

Platform-specific information should be preserved in metadata without
leaking platform-specific assumptions into the Agent Runtime.

## Standard outgoing model

``` python
class OutgoingMessage:
    channel_id: str
    recipient_id: str

    message_type: str
    text: str | None

    attachments: list
    reply_to_message_id: str | None

    metadata: dict
```

The Channel Manager passes this object to the appropriate adapter.

# 9. CENTRAL AGENT RUNTIME

The current web chat functionality should be refactored carefully.

The objective is to extract the actual agent execution logic from the
web-specific request handling.

Suggested service:

``` text
donna/
    intelligence/
        agent_runtime.py
        intent_router.py
        context_manager.py
        memory_manager.py
        permission_engine.py
        response_generator.py
```

Suggested execution method:

``` python
async def run_agent_turn(
    message,
    conversation_context,
    user_context,
    channel_context,
    permissions
):
    ...
```

The actual implementation should use the existing agent, LLM, MCP Hub
and storage abstractions wherever possible.

## Important architectural rule

The existing web dashboard must also use this shared Agent Runtime.

Do not create separate web, WhatsApp, Telegram or Slack agents.

There must be one central agent execution system. Only the communication
interface should change.

## Agent responsibilities

The Agent Runtime should:

-   Understand incoming messages.
-   Identify user intent.
-   Retrieve relevant context.
-   Determine whether tools are needed.
-   Execute approved and permitted tools.
-   Handle tool failures.
-   Generate a natural response.
-   Return a standardized result.

The Agent Runtime should not directly manage OpenWA, Telegram SDKs or
Slack APIs.

# 10. WHATSAPP INTEGRATION

## 10.1 Integration choice

Use the OpenWA project:

https://github.com/rmyndharis/OpenWA

OpenWA will operate as a separate WhatsApp gateway.

It is responsible for:

-   WhatsApp Web session management.
-   Receiving WhatsApp messages.
-   Sending WhatsApp messages.
-   Reporting connection status.
-   Forwarding incoming events to Donna.

Donna is responsible for:

-   Understanding messages.
-   Contact authorization.
-   Context and memory.
-   Tool execution.
-   Permissions.
-   Response generation.

## Separation requirement

Do not modify OpenWA's core unnecessarily. Treat it as an external
communication service.

## Suggested deployment

``` text
Donna Application
    |
    | Secured local webhook
    |
OpenWA Service
    |
    |
WhatsApp Web Session
    |
WhatsApp
```

OpenWA should run locally on the same machine initially.

The webhook must be protected with a shared secret or an equivalent
supported authentication mechanism. Validate any available signature,
timestamp and event information.

Do not expose an unauthenticated webhook to the public internet solely
for WhatsApp.

## WhatsApp risks

OpenWA is not the official WhatsApp Business Platform. It may depend on
reverse-engineered WhatsApp Web behaviour, and WhatsApp may change its
protocol or restrict accounts.

Therefore:

-   Use a dedicated WhatsApp number.
-   Do not send bulk unsolicited messages.
-   Apply rate limits.
-   Keep a global messaging kill switch.
-   Handle session expiry and reconnection.
-   Do not assume that the integration is guaranteed to remain
    operational.

For a future commercial deployment, evaluate the official WhatsApp
Business Platform.

# 11. CONTACT MANAGEMENT SYSTEM

This is a core feature.

The user must have full control over which contacts can communicate with
Donna.

## Contact authorization states

  -----------------------------------------------------------------------
  Mode                                Behaviour
  ----------------------------------- -----------------------------------
  Disabled                            Donna ignores the contact

  Suggest Only                        Donna creates a suggested response
                                      for owner approval

  Auto Reply                          Donna replies within configured
                                      permissions

  Priority / Human First              Donna notifies the owner and defers
                                      to human handling
  -----------------------------------------------------------------------

The default for an unknown contact must be **Disabled**.

## Contact profile

Every contact should have:

-   Contact name.
-   Phone number or platform identity.
-   Associated channel.
-   Authorization status.
-   Communication language.
-   Preferred response tone.
-   Working hours.
-   Allowed capabilities.
-   Contact-specific memory.
-   Conversation history.
-   Last interaction.
-   Automation preferences.

## Per-contact permissions

The user should be able to configure whether Donna can:

-   Reply automatically.
-   Check the owner's calendar availability.
-   Share availability windows.
-   Suggest meeting times.
-   Create calendar events.
-   Create Google Meet meetings.
-   Create tasks.
-   Access task information.
-   Access expense information.
-   Discuss selected personal information.
-   Draft or send emails.
-   Send attachments.
-   Initiate follow-up messages.

Each capability should have its own explicit permission.

A permission to check availability must not automatically imply
permission to book a meeting. A permission to draft an email must not
automatically imply permission to send it.

## Contact-level working hours

Example --- Rahul:

-   Auto-reply enabled.
-   Allowed hours: 9 AM--8 PM.
-   Language: Hinglish.
-   Can check availability.
-   Can suggest meetings.
-   Cannot access expense data.
-   Cannot initiate financial actions.

These rules must be enforced by backend code, not merely described to
the LLM.

# 12. CONVERSATION MANAGEMENT

Donna should support independent conversations across channels.

Examples:

-   WhatsApp conversation with Rahul.
-   Telegram conversation with Rahul.
-   Slack conversation with a work team.
-   Web dashboard conversation with Donna.

These conversations should be independently identifiable.

## Conversation features

-   Message history.
-   Conversation summaries.
-   Context retrieval.
-   Message timestamps.
-   Sender identity.
-   Channel information.
-   Message delivery status.
-   Processing status.
-   Error tracking.
-   Conversation archive.

## Cross-channel identity linking

The same person may contact Donna through WhatsApp and Telegram.

Donna should support optional linking of identities to one contact
profile.

However, linking identities must not automatically merge private
conversations.

The owner should be able to configure:

1.  Keep channel conversations separate.
2.  Share selected contact memory.
3.  Link conversations where explicitly authorized.

# 13. MEMORY MANAGEMENT

Donna needs multiple levels of memory.

## 13.1 Global user memory

Information relevant to the owner:

-   Preferred language.
-   Working hours.
-   Communication style.
-   Preferences.
-   Important ongoing projects.
-   Personal instructions.

## 13.2 Contact-specific memory

Information about individual contacts and their interactions:

-   Rahul is discussing a meeting.
-   A client requested a proposal.
-   A contact prefers communication in English.
-   A person is waiting for a response.

## 13.3 Conversation memory

Short-term context for the current conversation:

-   What was discussed earlier today?
-   What question did the person ask?
-   What information has already been provided?

## 13.4 Operational memory

Information about ongoing work and actions:

-   A meeting was scheduled.
-   An email draft is waiting for approval.
-   A task was created.
-   A follow-up is due.

## Memory security

-   Do not expose one contact's private context to another.
-   Do not treat untrusted inbound messages as instructions to modify
    global memory.
-   Provide a way to inspect and delete contact-specific memory.
-   Retain only the information needed for the configured purpose.
-   Keep sensitive information access-controlled.

# 14. INTENT DETECTION AND RESPONSE INTELLIGENCE

Donna should understand natural language rather than requiring rigid
commands.

  Intent                 Example
  ---------------------- --------------------------------
  General conversation   How are you?
  Availability           Are you free tomorrow?
  Meeting request        Can we schedule a meeting?
  Task request           Remind Yashraj to call me
  Follow-up              Any update on the proposal?
  Information request    Can you share the document?
  Email request          Send me the latest proposal
  Urgent message         Please call me immediately
  Expense request        What is the pending payment?
  Unknown                Unclear or unsupported request

Intent classification should guide tool selection, permissions and
response handling. It should not become a hard-coded list that prevents
normal conversation.

## Natural communication

Donna should:

-   Avoid robotic wording.
-   Understand conversational context.
-   Support English, Hindi, Marathi and Hinglish.
-   Respect configured response tone.
-   Avoid repeating unnecessary information.
-   Ask clarifying questions when required.
-   Escalate ambiguous or sensitive situations.
-   Avoid pretending that an action succeeded before confirming its
    result.

## Example

Incoming:

> Hey, can you check if Yashraj is free tomorrow around 4?

Donna:

> I'll check his calendar and get back to you.

Donna checks Calendar.

Response:

> He's available around 4 PM tomorrow. Would you like me to arrange a
> meeting?

Donna does not book the meeting unless the applicable permissions and
authorization requirements are satisfied.

# 15. TASK MANAGER INTEGRATION

The existing Task Manager should be connected through a supported API or
MCP server.

Do not recreate the task management application inside Donna. Donna
should act as an intelligent interface to the existing application.

## Suggested capabilities

-   Get pending tasks.
-   Create tasks.
-   Complete tasks.
-   Update task details.
-   Assign priorities.
-   Add due dates.
-   Retrieve task summaries.
-   Extract tasks from conversations.
-   Identify commitments.
-   Suggest reminders.

## Example

A contact messages:

> Please remind Yashraj to send the quotation by Friday.

Donna should:

1.  Identify a potential task.
2.  Extract the description and due date.
3.  Check task-creation permissions.
4.  Create the task if permitted.
5.  Confirm the result.

Task creation from another person's message should follow the owner's
configured policy.

# 16. EXPENSE TRACKER INTEGRATION

Connect the existing Expense Tracker using a documented API or MCP
tools.

## Suggested capabilities

-   Get today's expenses.
-   Get monthly expense summary.
-   Add an expense.
-   Search transactions.
-   Retrieve category-wise spending.
-   Identify pending payments.
-   Generate summaries.

## Security requirements

Expense information is private.

Contact permissions must explicitly restrict who can access it. By
default, external contacts should not be able to view financial
information.

Any financial operation that creates, modifies or commits a payment must
require a separate explicit authorization policy.

An inbound message must never be treated as sufficient authorization for
a financial transaction.

# 17. GMAIL INTEGRATION

Reuse the existing Gmail integration.

## Initial capabilities

-   Search emails.
-   Read email threads.
-   Summarize emails.
-   Identify important messages.
-   Find unanswered emails.
-   Extract follow-up requirements.
-   Create email drafts.
-   Suggest responses.
-   Extract invoice information.

## Sending policy

Initially:

-   Reading permitted emails can be automatic.
-   Summarizing permitted emails can be automatic.
-   Drafting can be automatic.
-   Sending an external email requires owner approval.

Future configurations may allow narrowly scoped automatic sending.

# 18. GOOGLE CALENDAR AND GOOGLE MEET

Reuse the existing Google Calendar integration.

Google Meet functionality should use Google Calendar event creation with
the supported conference-data functionality.

## Capabilities

-   Check availability.
-   Retrieve upcoming events.
-   Find free time slots.
-   Suggest meeting times.
-   Create meetings.
-   Reschedule meetings.
-   Cancel meetings.
-   Invite participants.
-   Generate Google Meet links.
-   Send meeting details.

## Meeting workflow

1.  A contact requests a meeting.
2.  Donna identifies the requested date and time.
3.  Donna checks contact permissions.
4.  Donna retrieves calendar availability.
5.  Donna suggests suitable options.
6.  The contact selects an option.
7.  Donna checks whether booking is authorized.
8.  If required, Donna requests owner approval.
9.  Donna creates the Calendar event.
10. Google Meet conference details are generated where requested.
11. Donna confirms the result.
12. Donna sends the meeting information through the originating channel.

Checking availability, suggesting a time, creating a calendar event and
sending invitations are separate operations with separate permissions.

# 19. AUTOMATION ENGINE

Donna should eventually support proactive actions.

Examples:

-   Daily morning briefing.
-   Important email notification.
-   Pending task reminder.
-   Meeting reminder.
-   Follow-up reminder.
-   Contact-specific follow-up.
-   Missed-message notification.
-   End-of-day summary.

## Suggested automation model

Each automation should have:

-   Unique ID.
-   Trigger type.
-   Conditions.
-   Target action.
-   Destination channel.
-   Schedule.
-   Permissions.
-   Enabled/disabled state.
-   Last execution.
-   Execution history.

## Scheduling implementation

Start with the simplest reliable solution compatible with the existing
application.

For the initial local-first release, consider:

-   SQLite-backed scheduled jobs.
-   A lightweight background worker.
-   Persistent job state.
-   Retry handling.
-   Execution logging.

Do not introduce Redis, BullMQ or Postgres without a concrete need.

A scheduler must survive application restarts and avoid duplicate
execution.

# 20. DATABASE DESIGN

Extend the existing SQLite database through migrations. Do not create a
completely separate database just for WhatsApp.

The following are proposed logical entities. Adapt names and columns to
existing repository conventions.

## channels

Stores connected communication channels.

Suggested fields:

-   id
-   channel_type
-   display_name
-   connection_status
-   configuration_reference
-   created_at
-   updated_at

## contacts

Stores people interacting with Donna.

Suggested fields:

-   id
-   display_name
-   created_at
-   updated_at

## contact_identities

Stores channel-specific identities belonging to a contact.

Suggested fields:

-   id
-   contact_id
-   channel_id
-   external_user_id
-   display_name
-   identity_verified
-   created_at

## contact_policies

Stores authorization and permission settings.

Suggested fields:

-   id
-   contact_id
-   channel_id
-   authorization_mode
-   allowed_capabilities
-   working_hours
-   allowed_languages
-   auto_reply_enabled
-   created_at
-   updated_at

## channel_conversations

Stores channel-specific conversation records.

Suggested fields:

-   id
-   channel_id
-   contact_id
-   external_conversation_id
-   status
-   summary
-   created_at
-   updated_at

## channel_messages

Stores incoming and outgoing messages.

Suggested fields:

-   id
-   conversation_id
-   external_message_id
-   direction
-   sender_id
-   message_type
-   content
-   attachment_metadata
-   delivery_status
-   timestamp

## contact_memories

Stores contact-specific memories.

Suggested fields:

-   id
-   contact_id
-   memory_type
-   memory_content
-   source_message_id
-   confidence
-   created_at
-   updated_at

## pending_approvals

Stores actions waiting for owner approval.

Suggested fields:

-   id
-   originating_channel
-   contact_id
-   action_type
-   action_payload
-   status
-   expires_at
-   created_at
-   resolved_at

## channel_events

Stores processed event IDs for deduplication.

Suggested fields:

-   id
-   channel_id
-   external_event_id
-   processing_status
-   received_at
-   processed_at

Use a uniqueness constraint on the appropriate channel/event identifier
combination.

## automation_rules

Stores scheduled and conditional workflows.

## audit_logs

Stores security-relevant events and action execution results.

All sensitive credentials should remain in the existing credential vault
or another appropriate secure secret store, not in ordinary database
columns.

# 21. APPROVAL AND PERMISSION SYSTEM

This is a critical safety component.

Donna should distinguish between:

1.  Permission to receive messages.
2.  Permission to generate responses.
3.  Permission to access information.
4.  Permission to prepare actions.
5.  Permission to execute actions.
6.  Permission to initiate communication.

## Initial permission defaults

  Operation                            Default
  ------------------------------------ -------------------
  Receive authorized contact message   Allowed
  Generate suggested reply             Allowed
  Automatic reply                      Contact setting
  Check calendar availability          Restricted
  Share availability                   Restricted
  Create calendar event                Approval required
  Send email                           Approval required
  Create task                          Configurable
  Read expense information             Owner only
  Modify financial records             Approval required
  Initiate external messages           Disabled

## Approval workflow

Example: A contact asks Donna to schedule a meeting.

1.  Donna prepares the meeting details.
2.  If owner approval is required, Donna creates an approval request.
3.  The dashboard displays the request.
4.  The owner reviews the requested action.
5.  The owner approves or rejects it.
6.  The backend validates the approval.
7.  The action executes.
8.  Donna records the result.
9.  Donna sends an appropriate response.

Approval requests must expire and must not be reusable.

An approval must be bound to the exact action payload. If the payload
changes, fresh approval is required.

# 22. SECURITY REQUIREMENTS

Security must be designed into the architecture from the beginning.

## Contact security

-   Default-deny for unknown contacts.
-   Explicit contact authorization.
-   Per-contact permissions.
-   Per-channel permissions.
-   No cross-contact data leakage.
-   Owner-controlled access revocation.

## Webhook security

-   Authenticate inbound events.
-   Validate event origin where supported.
-   Protect secrets.
-   Prevent replay where possible.
-   Deduplicate incoming events.
-   Apply request size limits.
-   Rate-limit abusive requests.

## AI security

-   Treat all incoming messages and attachments as untrusted data.
-   Do not execute instructions embedded in external documents without
    authorization.
-   Do not let external contacts change system configuration.
-   Do not allow contacts to bypass permissions through prompt
    injection.
-   Never expose API keys or OAuth tokens.
-   Enforce permissions in application code rather than relying on LLM
    instructions.

## Operational security

-   Global emergency kill switch.
-   Channel-level pause.
-   Contact-level pause.
-   Audit logs.
-   Error monitoring.
-   Secure credential storage.
-   Controlled message retries.
-   Safe reconnection handling.

## Privacy

The owner should be able to:

-   Inspect stored conversations.
-   Delete conversations.
-   Delete contact-specific memories.
-   Revoke contact permissions.
-   Disconnect a channel.
-   Disable all automation.

# 23. FRONTEND DASHBOARD SCOPE

Extend the existing Donna dashboard. Do not rebuild the frontend.

## 23.1 Channels

Purpose: Manage communication integrations.

Features:

-   Connected channels.
-   Add channel.
-   Connect/disconnect.
-   Connection status.
-   Last activity.
-   Error state.
-   Channel configuration.
-   Pause/resume.

## 23.2 Contacts

Purpose: Manage who can communicate with Donna.

Features:

-   Contact list.
-   Search contacts.
-   Add contact.
-   Import or discover contacts where supported.
-   Enable/disable.
-   Authorization mode.
-   Contact-specific permissions.
-   Preferred language.
-   Working hours.
-   Contact memory.
-   Conversation history.

## 23.3 Conversations

Purpose: Monitor communication.

Features:

-   All conversations.
-   Channel filter.
-   Contact filter.
-   Message history.
-   AI-generated summaries.
-   Processing status.
-   Manual response capability where supported.
-   Escalation indicator.

## 23.4 Approvals

Purpose: Review actions that need authorization.

Features:

-   Pending approvals.
-   Action description.
-   Originating contact.
-   Originating channel.
-   Requested operation.
-   Approve.
-   Reject.
-   Expiration.
-   Execution result.

## 23.5 Automations

Purpose: Manage scheduled workflows.

Features:

-   Create automation.
-   Edit automation.
-   Enable/disable.
-   Select trigger.
-   Select action.
-   Select destination channel.
-   View execution history.

## 23.6 Global Settings

Features:

-   AI provider configuration.
-   Existing model configuration.
-   Default language.
-   Default response style.
-   Default contact policy.
-   Automation controls.
-   Messaging rate limits.
-   Global kill switch.

# 24. API DESIGN

The following is a proposed API surface, not a claim that these
endpoints already exist. Follow existing FastAPI routing conventions.

## Channels

``` text
GET     /api/channels
POST    /api/channels
GET     /api/channels/{channel_id}
POST    /api/channels/{channel_id}/connect
POST    /api/channels/{channel_id}/disconnect
GET     /api/channels/{channel_id}/status
POST    /api/channels/{channel_id}/pause
POST    /api/channels/{channel_id}/resume
```

## Webhooks

``` text
POST    /api/webhooks/whatsapp
```

Future adapters may register their own routes or use a shared
event-ingestion mechanism.

## Contacts

``` text
GET     /api/contacts
POST    /api/contacts
GET     /api/contacts/{contact_id}
PATCH   /api/contacts/{contact_id}
DELETE  /api/contacts/{contact_id}

GET     /api/contacts/{contact_id}/policies
PUT     /api/contacts/{contact_id}/policies
```

## Conversations

``` text
GET     /api/conversations
GET     /api/conversations/{conversation_id}
GET     /api/conversations/{conversation_id}/messages
POST    /api/conversations/{conversation_id}/archive
```

## Approvals

``` text
GET     /api/approvals
GET     /api/approvals/{approval_id}
POST    /api/approvals/{approval_id}/approve
POST    /api/approvals/{approval_id}/reject
```

## Automations

``` text
GET     /api/automations
POST    /api/automations
PATCH   /api/automations/{automation_id}
DELETE  /api/automations/{automation_id}
POST    /api/automations/{automation_id}/enable
POST    /api/automations/{automation_id}/disable
```

All routes must use existing authentication and authorization
conventions.

# 25. SUGGESTED PROJECT STRUCTURE

This is a target organizational structure. Preserve existing modules
where practical instead of moving everything unnecessarily.

``` text
donna/
│
├── app.py
├── agent.py
├── hub.py
├── llm.py
├── store.py
├── google.py
│
├── channels/
│   ├── __init__.py
│   ├── base.py
│   ├── registry.py
│   ├── manager.py
│   │
│   ├── whatsapp/
│   │   ├── client.py
│   │   ├── webhook.py
│   │   ├── events.py
│   │   ├── normalizer.py
│   │   └── sender.py
│   │
│   ├── telegram/
│   │   └── ...
│   │
│   └── slack/
│       └── ...
│
├── intelligence/
│   ├── agent_runtime.py
│   ├── intent_router.py
│   ├── context_manager.py
│   ├── memory_manager.py
│   ├── permission_engine.py
│   └── response_generator.py
│
├── contacts/
│   ├── manager.py
│   ├── policies.py
│   └── identity.py
│
├── conversations/
│   ├── manager.py
│   └── history.py
│
├── approvals/
│   ├── manager.py
│   └── executor.py
│
├── automations/
│   ├── manager.py
│   ├── scheduler.py
│   └── worker.py
│
└── integrations/
    ├── tasks/
    ├── expenses/
    ├── gmail/
    ├── calendar/
    └── notion/
```

The structure may be adjusted to fit the actual repository after the
implementation audit.

# 26. IMPLEMENTATION ROADMAP

The implementation should be incremental. Each phase must be
independently testable and must preserve existing Donna functionality.

## PHASE 0 --- Repository Audit and Architecture Preparation

**Goal:** Understand the current implementation before changing it.

Tasks:

-   Inspect the complete repository.
-   Map existing FastAPI routes.
-   Understand the current `run_chat()` execution flow.
-   Inspect agent and MCP Hub interfaces.
-   Inspect existing database schema.
-   Identify existing migrations or schema initialization.
-   Inspect the approval system.
-   Inspect Google OAuth and credential handling.
-   Inspect frontend routing and state management.
-   Identify test coverage.
-   Document reusable modules.
-   Identify architectural risks.

Deliverables:

-   Existing architecture map.
-   Refactoring plan.
-   Database migration plan.
-   List of modules requiring modification.
-   Implementation checklist.

Acceptance criteria:

-   Existing behaviour is documented.
-   No unnecessary duplicate modules are introduced.
-   Existing functionality has a regression test baseline.

## PHASE 1 --- Shared Agent Runtime

**Goal:** Make Donna independent of any particular communication
channel.

Tasks:

1.  Extract shared agent execution from web-specific request handling.
2.  Define standardized incoming context.
3.  Define standardized agent result.
4.  Connect existing Groq LLM integration.
5.  Connect existing MCP Hub.
6.  Connect conversation context.
7.  Connect permission checks.
8.  Preserve streaming support for web chat.
9.  Add tests for shared agent execution.

Acceptance criteria:

-   Web chat continues working.
-   The same agent runtime can be called without a browser request.
-   Tool execution is not duplicated.
-   The agent does not depend on WhatsApp-specific logic.

## PHASE 2 --- Channel Manager and Contracts

**Goal:** Establish a reusable communication framework.

Tasks:

1.  Create BaseChannel interface.
2.  Create channel registry.
3.  Create Channel Manager.
4.  Define incoming message schema.
5.  Define outgoing message schema.
6.  Add channel lifecycle handling.
7.  Add connection status tracking.
8.  Add error and retry contracts.
9.  Add channel-level configuration.

Acceptance criteria:

-   A test channel can send an event into the shared Agent Runtime.
-   The channel manager can route responses back to the correct adapter.
-   No channel-specific dependency exists inside the core agent.

## PHASE 3 --- WhatsApp Integration

**Goal:** Establish real WhatsApp communication.

Tasks:

1.  Set up OpenWA as a separate service.
2.  Establish WhatsApp session.
3.  Add secured webhook endpoint.
4.  Receive inbound events.
5.  Normalize events.
6.  Identify senders.
7.  Add event deduplication.
8.  Route authorized messages to Donna.
9.  Send responses through OpenWA.
10. Track delivery and errors.
11. Add reconnection and pause controls.

Acceptance criteria:

-   WhatsApp messages can reach Donna.
-   Donna can respond through WhatsApp.
-   Duplicate events do not produce duplicate replies.
-   Invalid webhook events are rejected.
-   Web chat remains functional.

## PHASE 4 --- Contact Management and Permissions

**Goal:** Ensure Donna communicates only with authorized contacts.

Tasks:

1.  Create contact tables.
2.  Create channel identity mapping.
3.  Implement authorization modes.
4.  Implement per-contact permissions.
5.  Implement default-deny behaviour.
6.  Implement contact-level pause.
7.  Add working hours.
8.  Add language and response preferences.
9.  Build contact management dashboard.
10. Add audit logging.

Acceptance criteria:

-   Unknown contacts cannot invoke the AI.
-   Disabled contacts cannot trigger tool execution.
-   Permissions are enforced server-side.
-   Different contacts cannot access each other's private data.

## PHASE 5 --- Conversation and Memory

**Goal:** Give Donna continuity and contextual understanding.

Tasks:

1.  Persist channel conversations.
2.  Persist message history.
3.  Add conversation summaries.
4.  Add contact-specific memory.
5.  Add global memory access where appropriate.
6.  Implement memory retrieval.
7.  Implement memory update policies.
8.  Add memory deletion.
9.  Add conversation dashboard.

Acceptance criteria:

-   Donna remembers relevant conversation context.
-   Memory remains isolated by contact and authorization scope.
-   The same contact can have separately managed channel conversations.
-   The owner can inspect and delete stored information.

## PHASE 6 --- Approval and Action Execution

**Goal:** Safely support actions initiated from messaging channels.

Tasks:

1.  Extend the existing approval system.
2.  Create pending approval records.
3.  Bind approval to an exact action payload.
4.  Add expiration.
5.  Add approval dashboard.
6.  Add action execution validation.
7.  Add audit logs.
8.  Add owner notification.

Acceptance criteria:

-   Sensitive actions cannot execute without the required approval.
-   Rejected or expired actions cannot execute.
-   Duplicate approvals cannot trigger duplicate actions.
-   Tool execution results are recorded accurately.

## PHASE 7 --- Productivity Integrations

**Goal:** Connect Donna to existing productivity systems.

Recommended order:

1.  Existing Task Manager.
2.  Existing Expense Tracker.
3.  Gmail.
4.  Google Calendar.
5.  Google Meet through Calendar.

Tasks:

-   Inspect and document each existing integration.
-   Reuse existing MCP tools where possible.
-   Add missing tools.
-   Implement tool permissions.
-   Implement response handling.
-   Add action confirmation.
-   Test each integration through web and WhatsApp.

Acceptance criteria:

-   The same integration is available to web chat and WhatsApp.
-   Credentials are configured centrally.
-   Contact permissions restrict access.
-   Sensitive actions follow approval rules.

## PHASE 8 --- Automation Engine

**Goal:** Enable proactive assistance.

Tasks:

-   Persistent scheduler.
-   Scheduled reminders.
-   Daily briefing.
-   Follow-up detection.
-   Pending task reminders.
-   Meeting reminders.
-   Retry handling.
-   Execution history.
-   Automation pause/resume.

Acceptance criteria:

-   Jobs survive restarts.
-   Duplicate execution is prevented.
-   Failed jobs are observable.
-   Automations respect channel and contact permissions.

## PHASE 9 --- Future Channel Adapters

**Goal:** Demonstrate that the architecture is genuinely extensible.

Recommended order:

1.  Telegram.
2.  Slack.
3.  Discord or other requested platforms.

Each adapter must:

-   Implement BaseChannel.
-   Normalize events.
-   Use the same Agent Runtime.
-   Use the same contact and permission system.
-   Use the same MCP Hub.
-   Use the same approval system.
-   Have independent credentials and lifecycle management.

Acceptance criteria:

Adding a new channel must not require a new LLM integration, duplicated
agent runtime or duplicated Gmail/Calendar integration.

# 27. TESTING STRATEGY

Testing is mandatory at every phase.

## Unit testing

-   Message normalization.
-   Contact identity resolution.
-   Permission evaluation.
-   Intent routing.
-   Conversation retrieval.
-   Memory isolation.
-   Approval validation.
-   Event deduplication.

## Integration testing

-   FastAPI webhook.
-   OpenWA connectivity.
-   Groq agent execution.
-   MCP Hub execution.
-   Gmail.
-   Google Calendar.
-   Task Manager.
-   Expense Tracker.
-   SQLite persistence.

## Security testing

Test the following scenarios:

1.  Unknown contact sends a message.
2.  Disabled contact sends a message.
3.  Contact requests unauthorized financial information.
4.  Contact attempts to access another contact's conversation.
5.  Incoming message contains prompt injection.
6.  Webhook receives duplicate events.
7.  Webhook receives an invalid authentication token.
8.  An expired approval is submitted.
9.  A message tries to bypass an action approval.
10. The channel disconnects during message processing.

## Regression testing

Every change must preserve:

-   Existing web chat.
-   Existing MCP Hub behaviour.
-   Existing Gmail functionality.
-   Existing Calendar functionality.
-   Existing authentication.
-   Existing credential storage.
-   Existing conversation history.

# 28. NON-FUNCTIONAL REQUIREMENTS

## Performance

-   Avoid unnecessary LLM calls.
-   Reject unauthorized messages before AI processing.
-   Keep channel processing asynchronous.
-   Avoid blocking the FastAPI event loop.
-   Prevent long-running tool calls from blocking other conversations.
-   Use timeouts for external services.

## Reliability

-   Idempotent message processing.
-   Persistent processing state.
-   Retry handling.
-   Connection health checks.
-   Recoverable failures.
-   No duplicate action execution.

## Maintainability

-   Modular channel adapters.
-   Typed internal models.
-   Clear service boundaries.
-   Reusable integration tools.
-   Database migrations.
-   Automated tests.
-   Structured logs.

## Cost

The first version should continue using the existing Groq configuration
and local infrastructure.

Avoid unnecessary paid services. Use the existing SQLite database
initially. Do not introduce additional infrastructure unless justified
by actual workload.

# 29. CONFIGURATION AND ENVIRONMENT

Use the existing configuration system.

## Donna core

-   LLM provider.
-   Groq API key.
-   Model selection.
-   Agent settings.
-   Default language.
-   Default response tone.

## WhatsApp

-   OpenWA base URL.
-   Webhook authentication secret.
-   Session configuration.
-   Connection settings.
-   Message rate limits.

## Database

-   Existing SQLite database path.
-   Migration settings.

## Security

-   Webhook secrets.
-   Credential vault settings.
-   Approval expiry.
-   Global automation status.

## Integrations

-   Google OAuth configuration.
-   Task Manager API configuration.
-   Expense Tracker API configuration.
-   MCP server configuration.

Secrets must never be committed to Git.

# 30. ERROR HANDLING

Every channel and integration must return clear, structured errors.

  Error                          Expected behaviour
  ------------------------------ --------------------------------------------
  WhatsApp disconnected          Pause sending and notify owner
  Webhook authentication fails   Reject event
  Unknown contact                Ignore or apply configured safe handling
  Groq unavailable               Queue or report temporary failure
  MCP tool fails                 Explain that action could not be completed
  Calendar unavailable           Ask to try again later
  Approval expires               Do not execute
  Duplicate message              Return existing processing result
  Attachment unsupported         Explain limitation

Donna must never claim an action was completed when the external service
has not confirmed success.

# 31. RELEASE STRATEGY

## Version 1 --- Donna Multi-Channel Foundation

-   Shared Agent Runtime.
-   Channel Manager.
-   WhatsApp.
-   Contact authorization.
-   Conversation history.
-   Permission system.
-   Basic approval workflow.
-   Dashboard management.

## Version 2 --- Donna Productivity Assistant

-   Task Manager.
-   Expense Tracker.
-   Gmail.
-   Calendar.
-   Meeting scheduling.
-   Google Meet.
-   Contact memory.
-   Smart follow-ups.

## Version 3 --- Donna Automation System

-   Daily briefing.
-   Scheduled workflows.
-   Proactive notifications.
-   Conversation insights.
-   Voice notes.
-   Attachment understanding.

## Version 4 --- Donna Everywhere

-   Telegram.
-   Slack.
-   Discord.
-   Additional messaging platforms.
-   Voice interface.
-   Advanced channel routing.

# 32. DEFINITION OF DONE

The first major release is complete only when all of the following are
true:

-   [ ] Existing Donna web application works.
-   [ ] Shared Agent Runtime is used by web chat and WhatsApp.
-   [ ] Channel Manager is implemented.
-   [ ] WhatsApp connects through OpenWA.
-   [ ] Incoming WhatsApp messages reach Donna.
-   [ ] Donna can send WhatsApp responses.
-   [ ] Unknown contacts are denied by default.
-   [ ] Contacts can be enabled and disabled.
-   [ ] Contact permissions are configurable.
-   [ ] Conversations are persisted.
-   [ ] Contact memory is isolated.
-   [ ] Tool access is permission-controlled.
-   [ ] Sensitive actions use approval.
-   [ ] Duplicate events do not create duplicate responses or actions.
-   [ ] Webhook authentication is implemented.
-   [ ] A global kill switch exists.
-   [ ] Channel status is visible in the dashboard.
-   [ ] Logs and errors are accessible.
-   [ ] Regression and security tests pass.

# 33. IMPLEMENTATION RULES FOR THE AI CODING ASSISTANT

The following instructions are mandatory when implementing this project.

1.  Treat the existing repository as the source of truth.
2.  Inspect files before modifying them.
3.  Do not rebuild Donna from scratch.
4.  Do not duplicate existing integrations.
5.  Do not create a separate LLM runtime for WhatsApp.
6.  Do not put WhatsApp-specific logic inside the central Agent Runtime.
7.  Implement standardized channel interfaces.
8.  Implement backend-enforced authorization.
9.  Preserve existing web chat functionality.
10. Preserve existing Gmail, Calendar, MCP and credential functionality.
11. Use database migrations for schema changes.
12. Do not store API keys in plain text.
13. Do not expose webhook endpoints without authentication.
14. Add tests for every significant feature.
15. Do not introduce Redis, Postgres or paid infrastructure without
    justification.
16. Implement one phase at a time.
17. Do not silently skip incomplete tasks.
18. Document all configuration changes.
19. Report modified files and test results after every phase.
20. Never claim an integration works without testing it.

## Required implementation workflow

For every phase:

1.  Inspect the relevant existing code.
2.  Explain the proposed modifications.
3.  Identify affected modules.
4.  Implement the smallest coherent change.
5.  Run relevant tests.
6.  Fix regressions.
7.  Update documentation.
8.  Report completion and remaining work.
9.  Proceed to the next phase only when the current phase is stable.

# 34. FINAL PRODUCT DEFINITION

Donna is not simply a WhatsApp chatbot.

It is a centralized personal AI operating system with multiple
communication channels, shared intelligence, connected services,
controlled automation and persistent contextual understanding.

The user configures Donna once.

Donna can then communicate through multiple platforms, access authorized
tools, understand conversations, manage tasks, retrieve information,
coordinate meetings and perform approved actions.

The communication platform is only the interface.

The intelligence, memory, integrations and permission system belong to
Donna.

## FINAL ARCHITECTURAL PRINCIPLE

**One Donna.\
One central intelligence.\
One shared integration ecosystem.\
Multiple independent channels.\
One consistent permission and security system.**

------------------------------------------------------------------------

# RECOMMENDED IMPLEMENTATION ORDER

Do not ask the coding assistant to implement the entire document in one
go. That risks breaking the existing Donna application.

Start with these three milestones:

## Milestone 1 --- Architecture foundation

-   Repository audit.
-   Shared Agent Runtime.
-   Standard channel contracts.
-   Preserve existing web chat.

## Milestone 2 --- WhatsApp functionality

-   OpenWA setup.
-   Webhook.
-   Message receiving and sending.
-   Channel status.

## Milestone 3 --- Safety and intelligence

-   Contact allowlist.
-   Contact permissions.
-   Conversation storage.
-   Memory and approvals.

**Important recommendation:** Do not start with the WhatsApp UI or
contact management screen. First make sure the existing Donna agent can
operate independently of the web chat. That architectural change is what
makes every future channel possible without rebuilding the brain.
