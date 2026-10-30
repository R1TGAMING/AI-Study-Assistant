from langchain_openai import ChatOpenAI
from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain_openai import ChatOpenAI
from system_prompt import SYSTEM_PROMPT
from langchain_community.document_loaders import PyPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain.tools import tool
from langchain_chroma import Chroma
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.runnables.config import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from uuid import uuid4
import atexit
import shutil
import os
import tempfile
import streamlit as st

load_dotenv()

if "chroma_client" not in st.session_state:
    TEMP_DIR = tempfile.mkdtemp(prefix="chroma_")
    embeddings = HuggingFaceEmbeddings(
        model_name="LazarusNLP/all-indo-e5-small-v4",
        model_kwargs={"device": "cpu"},
        encode_kwargs={"normalize_embeddings": False},
    )
    st.session_state.chroma_client = Chroma(
        persist_directory=TEMP_DIR, embedding_function=embeddings
    )
    st.session_state.embeddings = embeddings
    st.session_state.temp_dir = TEMP_DIR

TEMP_DIR = st.session_state.temp_dir
chroma_client = st.session_state.chroma_client
embeddings = st.session_state.embeddings

checkpointer = InMemorySaver()
llm = ChatOpenAI(base_url=os.environ.get("OPENAI_BASE_URL"), model="all-in-free")


@tool
def search_similarity(query: str) -> str:
    """Search a similarity into documents with given query"""

    result_similarity = chroma_client.similarity_search(
        query=f"query: {query}",
    )

    if not result_similarity:
        return "Tidak ditemukan dokumen yang relevan."

    contents = [
        doc.page_content.replace("passage: ", "", 1) for doc in result_similarity
    ]
    return "\n\n".join(contents)


def cleanup():
    shutil.rmtree(TEMP_DIR)


def main():

    if "checkpointer" not in st.session_state:
        st.session_state.checkpointer = InMemorySaver()

    if "thread_id" not in st.session_state:
        st.session_state.thread_id = str(uuid4())

    thread_config: RunnableConfig = {
        "configurable": {"thread_id": st.session_state.thread_id}
    }

    if not os.environ.get("OPENAI_API_KEY"):
        print("Please set OPENAI_API_KEY environment variable")

    agent = create_agent(
        model=llm,
        system_prompt=SYSTEM_PROMPT,
        tools=[search_similarity],
        checkpointer=st.session_state.checkpointer,
    )

    st.sidebar.title("AI Asisten Belajar")

    if "messages" not in st.session_state:
        st.session_state.messages = []

    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    uploaded_file = st.sidebar.file_uploader("Upload materi", type=["pdf"])

    if uploaded_file:
        file_id = f"{uploaded_file.name}_{uploaded_file.size}"
        if st.session_state.get("processed_file_id") != file_id:
            with st.sidebar.spinner(text="Processing upload & indexing..."):
                with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                    tmp.write(uploaded_file.getvalue())
                    tmp_path = tmp.name

                loader = PyPDFLoader(tmp_path)
                documents = loader.load()
                text_splitter = RecursiveCharacterTextSplitter(
                    chunk_size=1000, chunk_overlap=150
                )
                doc_split = text_splitter.split_documents(documents)
                for document in doc_split:
                    document.page_content = f"passage: {document.page_content}"
                chroma_client.add_documents(doc_split)
                os.remove(tmp_path)

                st.session_state.processed_file_id = file_id
                st.sidebar.success(f"Berhasil upload: {uploaded_file.name}")

    if prompt := st.chat_input("What is up?"):
        with st.chat_message("user"):
            st.markdown(prompt)
        st.session_state.messages.append({"role": "user", "content": prompt})

        with st.chat_message("assistant"):
            with st.spinner("Thinking..."):

                def generate():
                    invoke = agent.stream(
                        {"messages": [{"role": "user", "content": prompt}]},
                        config=thread_config,
                        stream_mode="messages",
                    )

                    for chunk in invoke:
                        message: BaseMessage | None = None

                        if isinstance(chunk, tuple):
                            message = chunk[0]
                        elif isinstance(chunk, BaseMessage):
                            message = chunk

                        if (
                            isinstance(message, AIMessage)
                            and not getattr(message, "tool_calls", None)
                            and message.content
                        ):
                            yield message.content

                response = st.write_stream(generate())

            st.session_state.messages.append({"role": "assistant", "content": response})


if __name__ == "__main__":
    atexit.register(cleanup)
    main()
