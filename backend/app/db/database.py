import os

from sqlalchemy import Column, Float, Integer, MetaData, Table, Text, create_engine, inspect, text


DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./app.db")

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args)
metadata = MetaData()

projects = Table(
    "projects",
    metadata,
    Column("id", Text, primary_key=True),
    Column("repo_path", Text, nullable=False),
    Column("practice_root_path", Text, nullable=False),
    Column("target_extensions", Text, nullable=False),
    Column("project_summary", Text),
    Column("architecture", Text),
    Column("dependency_graph", Text),
    Column("analysis_status", Text, nullable=False, default="pending"),
    Column("assess_status", Text, nullable=False, default="pending"),
    Column("paper_source", Text),
    Column("paper_url", Text),
    Column("paper_title", Text),
    Column("paper_abstract", Text),
    Column("paper_content", Text),
    Column("paper_metadata", Text),
    Column("generated_repo_path", Text),
    Column("created_at", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
)

files = Table(
    "files",
    metadata,
    Column("id", Text, primary_key=True),
    Column("project_id", Text, nullable=False),
    Column("source_path", Text, nullable=False),
    Column("extension", Text, nullable=False),
    Column("is_target", Integer, nullable=False),
    Column("status", Text, nullable=False),
    Column("suitable", Integer),
    Column("suitable_reason", Text),
    Column("recommended_symbols", Text),
    Column("created_at", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
)

problems = Table(
    "problems",
    metadata,
    Column("id", Text, primary_key=True),
    Column("project_id", Text, nullable=False),
    Column("file_id", Text, nullable=False),
    Column("source_path", Text, nullable=False),
    Column("target_symbol", Text, nullable=False),
    Column("problem_type", Text, nullable=False),
    Column("prompt", Text, nullable=False),
    Column("starter_code", Text, nullable=False),
    Column("test_path", Text),
    Column("grading_method", Text, nullable=False, default="pytest"),
    Column("original_code", Text),
    Column("difficulty", Text),
    Column("context", Text),
    Column("parent_id", Text),
    Column("weight", Float, nullable=False, default=1.0),
    Column("depth", Integer, nullable=False, default=0),
    Column("unlock_dependencies", Text),
    Column("role_in_project", Text),
    Column("status", Text, nullable=False),
    Column("created_at", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
)

submissions = Table(
    "submissions",
    metadata,
    Column("id", Text, primary_key=True),
    Column("problem_id", Text, nullable=False),
    Column("code", Text, nullable=False),
    Column("passed", Integer, nullable=False),
    Column("stdout", Text),
    Column("stderr", Text),
    Column("duration_ms", Integer),
    Column("created_at", Text, nullable=False),
)

test_results = Table(
    "test_results",
    metadata,
    Column("id", Text, primary_key=True),
    Column("submission_id", Text, nullable=False),
    Column("test_nodeid", Text, nullable=False),
    Column("passed", Integer, nullable=False),
    Column("message", Text),
    Column("duration_ms", Integer),
)


def create_tables() -> None:
    metadata.create_all(bind=engine)
    _ensure_sqlite_columns()


def _ensure_sqlite_columns() -> None:
    if not DATABASE_URL.startswith("sqlite"):
        return

    inspector = inspect(engine)
    if "files" not in inspector.get_table_names():
        return

    existing_columns = {column["name"] for column in inspector.get_columns("files")}
    with engine.begin() as connection:
        if "suitable" not in existing_columns:
            connection.execute(text("ALTER TABLE files ADD COLUMN suitable INTEGER"))
        if "suitable_reason" not in existing_columns:
            connection.execute(text("ALTER TABLE files ADD COLUMN suitable_reason TEXT"))
        if "recommended_symbols" not in existing_columns:
            connection.execute(text("ALTER TABLE files ADD COLUMN recommended_symbols TEXT"))

    existing_problem_columns = {column["name"] for column in inspector.get_columns("problems")}
    with engine.begin() as connection:
        if "problem_type" not in existing_problem_columns:
            connection.execute(
                text("ALTER TABLE problems ADD COLUMN problem_type TEXT NOT NULL DEFAULT 'function_blank'")
            )
        if "grading_method" not in existing_problem_columns:
            connection.execute(text("ALTER TABLE problems ADD COLUMN grading_method TEXT NOT NULL DEFAULT 'pytest'"))
        if "original_code" not in existing_problem_columns:
            connection.execute(text("ALTER TABLE problems ADD COLUMN original_code TEXT"))
        if "difficulty" not in existing_problem_columns:
            connection.execute(text("ALTER TABLE problems ADD COLUMN difficulty TEXT"))
        if "context" not in existing_problem_columns:
            connection.execute(text("ALTER TABLE problems ADD COLUMN context TEXT"))
        if "parent_id" not in existing_problem_columns:
            connection.execute(text("ALTER TABLE problems ADD COLUMN parent_id TEXT"))
        if "weight" not in existing_problem_columns:
            connection.execute(text("ALTER TABLE problems ADD COLUMN weight REAL DEFAULT 1.0"))
        if "depth" not in existing_problem_columns:
            connection.execute(text("ALTER TABLE problems ADD COLUMN depth INTEGER DEFAULT 0"))
        if "unlock_dependencies" not in existing_problem_columns:
            connection.execute(text("ALTER TABLE problems ADD COLUMN unlock_dependencies TEXT"))
        if "role_in_project" not in existing_problem_columns:
            connection.execute(text("ALTER TABLE problems ADD COLUMN role_in_project TEXT"))

    existing_project_columns = {column["name"] for column in inspector.get_columns("projects")}
    with engine.begin() as connection:
        if "project_summary" not in existing_project_columns:
            connection.execute(text("ALTER TABLE projects ADD COLUMN project_summary TEXT"))
        if "architecture" not in existing_project_columns:
            connection.execute(text("ALTER TABLE projects ADD COLUMN architecture TEXT"))
        if "dependency_graph" not in existing_project_columns:
            connection.execute(text("ALTER TABLE projects ADD COLUMN dependency_graph TEXT"))
        if "analysis_status" not in existing_project_columns:
            connection.execute(text("ALTER TABLE projects ADD COLUMN analysis_status TEXT NOT NULL DEFAULT 'pending'"))
        if "assess_status" not in existing_project_columns:
            connection.execute(text("ALTER TABLE projects ADD COLUMN assess_status TEXT NOT NULL DEFAULT 'pending'"))
        if "paper_source" not in existing_project_columns:
            connection.execute(text("ALTER TABLE projects ADD COLUMN paper_source TEXT"))
        if "paper_url" not in existing_project_columns:
            connection.execute(text("ALTER TABLE projects ADD COLUMN paper_url TEXT"))
        if "paper_title" not in existing_project_columns:
            connection.execute(text("ALTER TABLE projects ADD COLUMN paper_title TEXT"))
        if "paper_abstract" not in existing_project_columns:
            connection.execute(text("ALTER TABLE projects ADD COLUMN paper_abstract TEXT"))
        if "paper_content" not in existing_project_columns:
            connection.execute(text("ALTER TABLE projects ADD COLUMN paper_content TEXT"))
        if "paper_metadata" not in existing_project_columns:
            connection.execute(text("ALTER TABLE projects ADD COLUMN paper_metadata TEXT"))
        if "generated_repo_path" not in existing_project_columns:
            connection.execute(text("ALTER TABLE projects ADD COLUMN generated_repo_path TEXT"))
