# Who Dis

This is a high-level pivot that separates the "juniors" from the "engineers." Moving from a Data-First to an Event-First architecture is exactly how you prevent the platform from turning into a spaghetti mess of race conditions.

You’re absolutely right: in a distributed system, Events are the truth; the Database is just a record of that truth.

🏗️ The Event-Driven Heartbeat
Following your refined Phase 1, the most critical "Next Step" is defining the Unified Event Contract. This is the shared language that your React frontend, Node.js backend, and Redis Pub/Sub will speak.

1. The Event Envelope
Every event in the system should follow a strict, versioned structure. This allows you to add features (like reactions or message editing) without breaking the older versions of your client.

The Event Schema (v1)

JSON
{
  "id": "uuid-v4",
  "type": "MESSAGE_CREATED",
  "version": "1.0",
  "timestamp": 1700000000,
  "sender_id": "user_uuid",
  "payload": { ... },
  "metadata": {
    "correlation_id": "uuid-v4", // Trace the event through the system
    "sequence_id": 456           // Crucial for ordering (Message Ordering hurdle)
  }
}
⚡ Solving the "Real Hurdles"
Before we write a single line of business logic, we need the "Infrastructure for Reliability" you pointed out.

🛡️ Reconnect Logic: The "Sync" Event
To solve State Drift and Message Ordering after a user wakes their laptop from sleep, we won't just "reconnect." We will trigger a SYNC_REQUEST.

Client stores the last_sequence_id it successfully rendered.

On Reconnect, the client sends: SYNC_REQUEST(channel_id, last_sequence_id).

Backend fetches the delta from Postgres:

M 
delta
​
 ={m∈Channel∣m 
seq
​
 >m 
client_last_seen
​
 }
Client merges the delta, ensuring no messages were lost during the "Ghost Presence" phase.

👻 Ghost Presence: The Redis "Dead Man's Switch"
To kill "Ghost Users" (the ones who close the tab without hitting 'Logout'), we use a heartbeat + TTL (Time To Live) strategy in Redis.

WebSocket Heartbeat: Client sends a PING every 20 seconds.

Redis Key: presence:user_uuid with an expiry of 45 seconds.

On Expiry: If no PING arrives, the key vanishes. We use Redis Keyspace Notifications to trigger a USER_DISCONNECTED event to everyone in that server.

🗺️ The Revised Architecture Map (Event Flow)
In this event-first model, the backend becomes a State Manager and Event Router.

Action: User types "Hello" and hits Enter.

Frontend: Instantly adds the message to the UI (Optimistic UI) and sends a MESSAGE_SEND event.

Backend (Gateway):

Validates user_id permissions (Is user in channel?).

Assigns a sequence_id (The "True Order").

Persists to PostgreSQL (The "Persistent State").

Publishes to Redis Pub/Sub (The "Realtime Fan-out").

Backend (Routing): All connected server instances see the Redis event and push MESSAGE_CREATED to their respective clients.

Frontend: Receives MESSAGE_CREATED, matches the correlation_id to the optimistic message, and updates the status to "Sent."

🎯 Next Step: Defining the Core Event Taxonomy
Since we are skipping the DB Schema for now, would you like me to draft the Core Event List (the Phase 1.5 deliverable) including the payload structures for the Realtime Messaging and Presence systems? This will effectively become the "API Documentation" for your WebSockets.
