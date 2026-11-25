"""RAG policy search tool over ChromaDB.

The embeddings come from llm.get_embeddings() (OpenAI, Gemini or local FastEmbed). Each model makes
vectors of a different size, so the provider + model that built the index are saved in
chroma_db/embeddings.json. If they change, the old index is moved to chroma_db.old-<timestamp>
(never deleted) and a new one is built.
"""

import json
import shutil
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from langchain_community.document_loaders import TextLoader
from langchain_community.vectorstores import Chroma
from langchain_core.documents import Document
from langchain_core.tools import tool
from langchain_text_splitters import RecursiveCharacterTextSplitter

from langchain_bot.llm import describe_embeddings, get_embeddings

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
POLICIES_DIR = PROJECT_ROOT / "policies"
CHROMA_DIR = PROJECT_ROOT / "chroma_db"
EMBEDDINGS_META = "embeddings.json"

_vector_store = None


def _index_has_files():
    return CHROMA_DIR.exists() and any(CHROMA_DIR.iterdir())


def _saved_embeddings():
    try:
        return json.loads((CHROMA_DIR / EMBEDDINGS_META).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None  # no metadata: an index from before embeddings.json existed


def _move_index_aside():
    target = CHROMA_DIR.with_name(f"{CHROMA_DIR.name}.old-{datetime.now():%Y%m%d-%H%M%S}")
    shutil.move(str(CHROMA_DIR), str(target))
    return target


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

    wanted = describe_embeddings()
    if _index_has_files() and _saved_embeddings() != wanted:
        old = _move_index_aside()
        print(f"Embeddings changed to {wanted['provider']} / {wanted['model']}: "
              f"moved the old index to {old.name}/ and building a new one")

    store = Chroma(persist_directory=str(CHROMA_DIR), embedding_function=get_embeddings())
    # Record what builds this index (before adding documents, so a failed build is retried in place)
    (CHROMA_DIR / EMBEDDINGS_META).write_text(json.dumps(wanted, indent=2), encoding="utf-8")

    # Check the document count, not just whether chroma_db/ has files: a build that failed
    # part-way (bad API key, network error) leaves an empty collection behind.
    if store._collection.count() > 0:
        print("Loaded existing vector store from chroma_db/")
    else:
        print("Loading policy documents into a new vector store...")
        chunks = split_documents(load_policy_documents())
        store.add_documents(chunks)
        print(f"Vector store created with {len(chunks)} chunks")

    _vector_store = store
    return _vector_store


@tool
def search_policies(query: str) -> str:
    """Search the store's policy and FAQ documents: returns, refunds, shipping,
    cancellations. Use ONLY for general policy/FAQ questions.
    Do NOT use it for the user's own orders, payments or returns - use the customer data tools."""
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
