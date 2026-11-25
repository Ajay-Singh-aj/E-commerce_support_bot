"""LangChain SQL Database Toolkit over ecommerce.db."""

from pathlib import Path

from dotenv import load_dotenv
from langchain_community.agent_toolkits import SQLDatabaseToolkit
from langchain_community.utilities import SQLDatabase
from langchain_openai import ChatOpenAI

load_dotenv()

DB_PATH = Path(__file__).resolve().parents[2] / "ecommerce.db"


def get_database():
    return SQLDatabase.from_uri(f"sqlite:///{DB_PATH.as_posix()}")


def get_sql_tools():
    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)
    toolkit = SQLDatabaseToolkit(db=get_database(), llm=llm)
    return toolkit.get_tools()
