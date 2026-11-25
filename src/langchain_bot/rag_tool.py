"""RAG policy search tool over ChromaDB."""

from pathlib import Path

from dotenv import load_dotenv
from langchain_community.document_loaders import TextLoader
from langchain_community.vectorstores import Chroma
from langchain_core.documents import Document
from langchain_core.tools import tool
from langchain_openai import OpenAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
POLICIES_DIR = PROJECT_ROOT / "policies"
CHROMA_DIR = PROJECT_ROOT / "chroma_db"

_vector_store = None


def load_policy_documents() -> list[Document]:
    documents = []
    for file_path in sorted(POLICIES_DIR.glob("*.txt")):
        docs = TextLoader(str(file_path), encoding="utf-8").load()
        for doc in docs:
            doc.metadata["source"] = file_path.name
        documents.extend(docs)
    return documents


def split_documents(documents):
    splitter = RecursiveCharacterTextSplitter(chunk_size=800, chunk_overlap=150)
    return splitter.split_documents(documents)


def get_vector_store():
    global _vector_store
    if _vector_store is not None:
        return _vector_store

    embeddings = OpenAIEmbeddings(model="text-embedding-3-small")

    if CHROMA_DIR.exists() and any(CHROMA_DIR.iterdir()):
        print("Loaded existing vector store from chroma_db/")
        _vector_store = Chroma(
            persist_directory=str(CHROMA_DIR),
            embedding_function=embeddings,
        )
    else:
        print("Loading policy documents into a new vector store...")
        chunks = split_documents(load_policy_documents())
        _vector_store = Chroma.from_documents(
            documents=chunks,
            embedding=embeddings,
            persist_directory=str(CHROMA_DIR),
        )
        print(f"Vector store created with {len(chunks)} chunks")

    return _vector_store


@tool
def search_policies(query: str) -> str:
    """Search the store's policy and FAQ documents: returns, refunds, shipping,
    cancellations. Use ONLY for general policy/FAQ questions.
    Do NOT use it for the user's own orders, payments or returns - use the SQL tools."""
    results = get_vector_store().similarity_search(query, k=4)

    if not results:
        return "No relevant policy information found."

    chunks = []
    for doc in results:
        chunks.append(f"[Source: {doc.metadata.get('source', 'unknown')}]\n{doc.page_content}")
    return "\n\n---\n\n".join(chunks)


def initialize_vector_store():
    """Build or load the vector store when the app starts."""
    get_vector_store()


__all__ = ["search_policies", "initialize_vector_store"]
