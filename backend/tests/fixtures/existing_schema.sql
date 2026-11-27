-- Copy of the existing database schema (tables this app reads or writes, plus
-- their foreign-key parents), used by test_database_integration.py.
-- Unqualified names: the test creates them in a throwaway schema via search_path.
-- The real schema is owned by another application's Alembic migrations; this
-- app never runs DDL against it.

CREATE TABLE organizations (
	"name" varchar(255) NOT NULL,
	plan varchar(50) DEFAULT 'free'::character varying NOT NULL,
	timezone varchar(64) DEFAULT 'UTC'::character varying NOT NULL,
	settings jsonb DEFAULT '{}'::jsonb NOT NULL,
	id uuid NOT NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	updated_at timestamptz DEFAULT now() NOT NULL,
	CONSTRAINT organizations_pkey PRIMARY KEY (id)
);

CREATE TABLE agents (
	organization_id uuid NOT NULL,
	"name" varchar(255) NOT NULL,
	prompt text NOT NULL,
	voice varchar(100) NULL,
	"language" varchar(50) NULL,
	"configuration" jsonb DEFAULT '{}'::jsonb NOT NULL,
	id uuid NOT NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	updated_at timestamptz DEFAULT now() NOT NULL,
	CONSTRAINT agents_pkey PRIMARY KEY (id),
	CONSTRAINT agents_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE CASCADE
);

CREATE TABLE audit_logs (
	organization_id uuid NOT NULL,
	actor varchar(255) NOT NULL,
	"action" varchar(255) NOT NULL,
	"timestamp" timestamptz DEFAULT now() NOT NULL,
	id uuid NOT NULL,
	CONSTRAINT audit_logs_pkey PRIMARY KEY (id),
	CONSTRAINT audit_logs_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE CASCADE
);

CREATE TABLE clients (
	organization_id uuid NOT NULL,
	external_client_id varchar(100) NOT NULL,
	"name" varchar(255) NOT NULL,
	phone varchar(32) NOT NULL,
	"language" varchar(50) NULL,
	id uuid NOT NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	updated_at timestamptz DEFAULT now() NOT NULL,
	CONSTRAINT clients_pkey PRIMARY KEY (id),
	CONSTRAINT uq_clients_org_external UNIQUE (organization_id, external_client_id),
	CONSTRAINT clients_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE CASCADE
);

CREATE TABLE invoices (
	organization_id uuid NOT NULL,
	client_id uuid NOT NULL,
	external_invoice_id varchar(100) NOT NULL,
	amount numeric(14, 2) NOT NULL,
	due_date date NOT NULL,
	status varchar(30) DEFAULT 'pending'::character varying NOT NULL,
	id uuid NOT NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	updated_at timestamptz DEFAULT now() NOT NULL,
	CONSTRAINT invoices_pkey PRIMARY KEY (id),
	CONSTRAINT uq_invoices_org_external UNIQUE (organization_id, external_invoice_id),
	CONSTRAINT invoices_client_id_fkey FOREIGN KEY (client_id) REFERENCES clients(id) ON DELETE CASCADE,
	CONSTRAINT invoices_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE CASCADE
);

CREATE TABLE workflows (
	organization_id uuid NOT NULL,
	trigger_type varchar(50) NOT NULL,
	conditions jsonb DEFAULT '{}'::jsonb NOT NULL,
	actions jsonb DEFAULT '{}'::jsonb NOT NULL,
	id uuid NOT NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	updated_at timestamptz DEFAULT now() NOT NULL,
	CONSTRAINT workflows_pkey PRIMARY KEY (id),
	CONSTRAINT workflows_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE CASCADE
);

CREATE TABLE call_tasks (
	organization_id uuid NOT NULL,
	client_id uuid NOT NULL,
	workflow_id uuid NULL,
	scheduled_at timestamptz NOT NULL,
	status varchar(30) DEFAULT 'queued'::character varying NOT NULL,
	id uuid NOT NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	updated_at timestamptz DEFAULT now() NOT NULL,
	CONSTRAINT call_tasks_pkey PRIMARY KEY (id),
	CONSTRAINT call_tasks_client_id_fkey FOREIGN KEY (client_id) REFERENCES clients(id) ON DELETE CASCADE,
	CONSTRAINT call_tasks_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE CASCADE,
	CONSTRAINT call_tasks_workflow_id_fkey FOREIGN KEY (workflow_id) REFERENCES workflows(id) ON DELETE SET NULL
);

CREATE TABLE calls (
	organization_id uuid NOT NULL,
	client_id uuid NOT NULL,
	agent_id uuid NULL,
	provider_call_id varchar(255) NULL,
	status varchar(30) DEFAULT 'initiated'::character varying NOT NULL,
	duration int4 NULL,
	id uuid NOT NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	updated_at timestamptz DEFAULT now() NOT NULL,
	CONSTRAINT calls_pkey PRIMARY KEY (id),
	CONSTRAINT calls_agent_id_fkey FOREIGN KEY (agent_id) REFERENCES agents(id) ON DELETE SET NULL,
	CONSTRAINT calls_client_id_fkey FOREIGN KEY (client_id) REFERENCES clients(id) ON DELETE CASCADE,
	CONSTRAINT calls_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE CASCADE
);
CREATE UNIQUE INDEX uq_calls_provider_call_id ON calls USING btree (provider_call_id) WHERE (provider_call_id IS NOT NULL);

CREATE TABLE call_actions (
	call_id uuid NOT NULL,
	action_type varchar(50) NOT NULL,
	action_status varchar(30) DEFAULT 'pending'::character varying NOT NULL,
	"result" jsonb DEFAULT '{}'::jsonb NOT NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	id uuid NOT NULL,
	CONSTRAINT call_actions_pkey PRIMARY KEY (id),
	CONSTRAINT call_actions_call_id_fkey FOREIGN KEY (call_id) REFERENCES calls(id) ON DELETE CASCADE
);

CREATE TABLE call_events (
	call_id uuid NOT NULL,
	event_type varchar(50) NOT NULL,
	event_data jsonb DEFAULT '{}'::jsonb NOT NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	id uuid NOT NULL,
	CONSTRAINT call_events_pkey PRIMARY KEY (id),
	CONSTRAINT call_events_call_id_fkey FOREIGN KEY (call_id) REFERENCES calls(id) ON DELETE CASCADE
);

CREATE TABLE call_transcripts (
	call_id uuid NOT NULL,
	speaker varchar(20) NOT NULL,
	"text" text NOT NULL,
	"timestamp" timestamptz DEFAULT now() NOT NULL,
	id uuid NOT NULL,
	CONSTRAINT call_transcripts_pkey PRIMARY KEY (id),
	CONSTRAINT call_transcripts_call_id_fkey FOREIGN KEY (call_id) REFERENCES calls(id) ON DELETE CASCADE
);
