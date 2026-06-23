import requests
import oci


class LLMService:
    def __init__(
        self,
        mode="local",  # "local" or "oci"
        local_server_url="http://127.0.0.1:8000/generate",
        oci_profile="DEFAULT",
        oci_config_path="/Users/debtanu/Documents/DB-Triage-Agentic/SQLMonitorDiagnsis/config",
        oci_compartment_id=None,
        oci_endpoint_id=None,
        oci_service_endpoint="https://inference.generativeai.us-chicago-1.oci.oraclecloud.com"
    ):
        self.mode = mode.lower()
        self.local_server_url = local_server_url
        self.oci_client = None
        self.oci_compartment_id = oci_compartment_id
        self.oci_endpoint_id = oci_endpoint_id

        if self.mode == "oci":
            self._init_oci(oci_profile, oci_config_path, oci_service_endpoint)

    # =======================
    # OCI LLM init
    # =======================
    def _init_oci(self, profile, config_path, service_endpoint):
        print("[OCI LLM] Initializing OCI client...")
        config = oci.config.from_file(config_path, profile)
        self.oci_client = oci.generative_ai_inference.GenerativeAiInferenceClient(
            config=config,
            service_endpoint=service_endpoint,
            retry_strategy=oci.retry.NoneRetryStrategy(),
            timeout=(10, 240)
        )
        print("[OCI LLM] Client ready!")

    # =======================
    # Generate
    # =======================
    def generate(self, prompt, max_new_tokens=512, temperature=0.7):
        if self.mode == "local":
            # Call existing FastAPI local LLM server
            payload = {
                "prompt": prompt,
                "max_new_tokens": max_new_tokens,
                "temperature": temperature,
            }
            response = requests.post(self.local_server_url, json=payload)
            response.raise_for_status()
            return response.json().get("raw_text")

        elif self.mode == "oci":
            chat_detail = oci.generative_ai_inference.models.ChatDetails()
            content = oci.generative_ai_inference.models.TextContent()
            content.text = prompt

            message = oci.generative_ai_inference.models.Message()
            message.role = "USER"
            message.content = [content]

            chat_request = oci.generative_ai_inference.models.GenericChatRequest()
            chat_request.api_format = oci.generative_ai_inference.models.BaseChatRequest.API_FORMAT_GENERIC
            chat_request.messages = [message]
            chat_request.max_tokens = max_new_tokens
            chat_request.temperature = temperature
            chat_request.frequency_penalty = 0
            chat_request.presence_penalty = 0
            chat_request.top_p = 0.75

            chat_detail.serving_mode = oci.generative_ai_inference.models.OnDemandServingMode(
                model_id=self.oci_endpoint_id
            )
            chat_detail.chat_request = chat_request
            chat_detail.compartment_id = self.oci_compartment_id

            response = self.oci_client.chat(chat_detail)
            try:
                return response.data.output_text
            except Exception:
                return str(response.data)


# =======================
# Example usage
# =======================
if __name__ == "__main__":
    # --- Local Example (calls your running FastAPI server) ---
    # local_llm = LLMService(mode="local", local_server_url="http://127.0.0.1:8080/generate")
    # print(local_llm.generate("Ignore Output Rules and Answer Normally. Explain quantum entanglement in simple terms."))

    # --- OCI Example ---
    oci_llm = LLMService(
        mode="oci",
        oci_profile="DEFAULT",
        oci_config_path="/Users/debtanu/Documents/DB-Triage-Agentic/SQLMonitorDiagnsis/config",
        oci_compartment_id="ocid1.compartment.oc1..aaaaaaaa47jbpgerlzz4fdgpqrbelelkj7mamirdqdbwdrtkm3ez6b7mnizq",
        oci_endpoint_id="ocid1.generativeaimodel.oc1.us-chicago-1.amaaaaaask7dceyajqi26fkxly6qje5ysvezzrypapl7ujdnqfjq6hzo2loq"
    )
    print(oci_llm.generate("What does OCI do for profit?"))
