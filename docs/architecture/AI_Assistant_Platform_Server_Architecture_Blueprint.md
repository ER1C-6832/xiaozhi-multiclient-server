# AI Assistant Platform Server Architecture Blueprint

## 1. Vision and Positioning

This server is designed as a multi-user, multi-device, multi-agent AI
Assistant Platform.

The goal is not only to provide a Xiaozhi-compatible voice backend, but
to build a scalable assistant runtime platform supporting:

-   Windows / macOS / Android / iOS clients
-   Multiple users
-   Multiple devices per user
-   Multiple configurable agents
-   Cloud and device-side tools
-   Voice interaction pipeline
-   Data synchronization
-   Long-term assistant evolution

The Xiaozhi protocol is treated as one compatible gateway, not the
entire platform boundary.

------------------------------------------------------------------------

# 2. Overall Architecture

    +------------------------------------------------+
    |                 Client Layer                   |
    | Windows | macOS | Android | iOS | ESP32        |
    +-------------------------+----------------------+
                              |
                              |
    +-------------------------v----------------------+
    |              Gateway Layer                     |
    |                                                |
    | WebSocket Gateway                              |
    | Authentication                                 |
    | Device Connection                              |
    | Audio Transport                                |
    | Protocol Adapter (Xiaozhi compatible)          |
    +-------------------------+----------------------+
                              |
                              |
    +-------------------------v----------------------+
    |              Agent Platform                    |
    |                                                |
    | User Management                                |
    | Device Management                              |
    | Agent Management                               |
    | Configuration Management                       |
    +-------------------------+----------------------+
                              |
                              |
    +-------------------------v----------------------+
    |              Agent Runtime                     |
    |                                                |
    | Session Runtime                                |
    | Context Assembly                               |
    | Prompt Management                              |
    | Model Routing                                  |
    | Tool Invocation                                |
    | Streaming Coordination                         |
    +-------------------------+----------------------+
                              |
              +---------------+---------------+
              |               |               |
              v               v               v

            ASR             LLM             TTS

              |
              |
    +---------v--------------------------------------+
    |              Tool Runtime                      |
    |                                                |
    | Tool Router                                    |
    | MCP Management                                 |
    | Permission Check                               |
    | Execution Tracking                             |
    +-------------------------+----------------------+

              |
              |
    +---------v--------------------------------------+
    |              Data Layer                        |
    |                                                |
    | PostgreSQL                                     |
    | Redis                                          |
    | Vector Database                                |
    +------------------------------------------------+

------------------------------------------------------------------------

# 3. Core Architectural Principles

## 3.1 Agent is Configuration, Runtime is Execution

Agent is not an independent process.

Agent represents:

-   personality
-   prompt
-   model selection
-   voice configuration
-   tools
-   memory policy

Runtime executes the selected Agent.

Example:

    Agent:
      Work Assistant

    Configuration:
      LLM: Qwen
      Voice: Female
      Tools:
        calendar
        notes
        mail

Runtime:

    Load Agent
          |
    Create Session
          |
    Execute Conversation

------------------------------------------------------------------------

## 3.2 Runtime Should Be Stateless

Runtime processes should not permanently own user state.

State should live externally.

Runtime:

    Request
     |
    Load state
     |
    Execute
     |
    Persist result

State storage:

-   Redis: temporary runtime state
-   PostgreSQL: persistent business data
-   Vector database: semantic memory

Benefits:

-   horizontal scaling
-   fault recovery
-   multiple runtime workers
-   easier deployment

------------------------------------------------------------------------

# 4. System Modules

## 4.1 Gateway Layer

Responsibilities:

-   Client connection
-   Authentication
-   Audio stream transport
-   Protocol adaptation
-   Connection lifecycle

Supports:

-   WebSocket
-   Xiaozhi protocol
-   Future WebRTC/API access

------------------------------------------------------------------------

# 4.2 Agent Platform Layer

## User Management

Stores:

-   accounts
-   identity
-   preferences

## Device Management

A device represents:

-   Windows application
-   macOS application
-   Android application
-   iOS application

Device information:

    Device
     |
     + user
     + type
     + capabilities
     + status
     + authentication token

## Agent Management

Agent stores:

    Agent

    name

    system prompt

    model configuration

    voice configuration

    tool policy

    memory policy

One Agent can bind multiple devices.

------------------------------------------------------------------------

# 5. Agent Runtime Design

Runtime responsibilities:

## Session creation

Input:

    User
    Device
    Agent

Output:

    Session

------------------------------------------------------------------------

## Context Assembly

Runtime gathers:

-   system prompt
-   conversation history
-   short memory
-   long memory
-   available tools

------------------------------------------------------------------------

## Streaming Execution

Pipeline:

    Audio
     |
    ASR
     |
    Text
     |
    LLM
     |
    Tool Decision
     |
    TTS
     |
    Audio Output

Supports:

-   partial ASR
-   streaming LLM
-   streaming TTS
-   interruption

------------------------------------------------------------------------

# 6. Voice Pipeline

## ASR Layer

Responsible for:

-   speech recognition
-   streaming transcription

Adapter architecture:

    ASR Interface

     |
     +-- Whisper
     +-- FunASR
     +-- Custom Model

Custom ASR models can be integrated.

------------------------------------------------------------------------

## LLM Layer

Responsible for:

-   reasoning
-   tool selection
-   response generation

Supports:

-   cloud models
-   local models
-   multiple providers

------------------------------------------------------------------------

## TTS Layer

Responsible for:

-   voice synthesis
-   streaming playback

Supports:

-   multiple voices
-   multiple languages
-   provider switching

------------------------------------------------------------------------

# 7. Tool Runtime and MCP Architecture

Tools are abstract capabilities.

Unified interface:

    Tool Runtime

     |
     +-- Cloud MCP
     |
     +-- Device MCP
     |
     +-- External MCP

Example:

Cloud:

    calendar.query
    weather.search

Device:

    notes.create
    open.application
    file.operation

External:

    third-party services

------------------------------------------------------------------------

# 8. MCP Routing

LLM does not directly execute tools.

Flow:

    LLM

     |
    Tool Call

     |
    Tool Router

     |
    Permission Check

     |
    MCP Execution

     |
    Result

     |
    LLM Continue

Advantages:

-   security
-   auditing
-   permissions
-   multi-device routing

------------------------------------------------------------------------

# 9. Data Architecture

## PostgreSQL

Primary business database.

Stores:

-   users
-   devices
-   agents
-   agent-device relationships
-   configurations
-   conversations
-   messages
-   permissions

------------------------------------------------------------------------

## Redis

Runtime state database.

Stores:

-   active sessions
-   connection state
-   temporary context
-   streaming state
-   task coordination

Example:

    session_id

    device_id

    agent_id

    current_status

    audio_state

------------------------------------------------------------------------

## Vector Database

Memory storage.

Stores:

-   user preferences
-   semantic memories
-   historical knowledge

------------------------------------------------------------------------

# 10. Core Data Model

## User

    User

    id

    profile

    preferences

------------------------------------------------------------------------

## Device

    Device

    id

    user_id

    type

    capabilities

    status

------------------------------------------------------------------------

## Agent

    Agent

    id

    owner

    prompt

    model_config

    voice_config

    tool_policy

    memory_policy

------------------------------------------------------------------------

## Agent Device Binding

    AgentDevice

    agent_id

    device_id

    permission

------------------------------------------------------------------------

## Session

Short lifecycle:

    Session

    id

    user_id

    device_id

    agent_id

    status

    created_time

------------------------------------------------------------------------

## Conversation

Long lifecycle:

    Conversation

    id

    user_id

    agent_id

    title

Messages:

    Message

    conversation_id

    role

    content

    timestamp

------------------------------------------------------------------------

# 11. Runtime State Relationships

    User

     |
     +----------------+

    Device          Agent

                      |
                      |

                Conversation

                      |

                  Messages


    Session

    (connects Device + Agent temporarily)

------------------------------------------------------------------------

# 12. Final Architecture Summary

The final platform consists of:

    Gateway

       |

    Agent Platform

       |

    Agent Runtime

       |

    ASR + LLM + TTS

       |

    Tool Runtime

       |

    Memory + Data Platform

The system is designed to evolve from a Xiaozhi-compatible voice server
into a complete multi-user AI assistant platform.
