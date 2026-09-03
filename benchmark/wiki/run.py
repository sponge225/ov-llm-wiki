import os
import sys
import yaml
import importlib
from argparse import ArgumentParser
from pathlib import Path

sys.path.append(str(Path(__file__).parent))

from src.core.logger import setup_logging
# ==========================================
# 1. Environment Initialization
# ==========================================
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = SCRIPT_DIR

try:
    from src.pipeline import BenchmarkPipeline
    from src.core.vector_store import VikingStoreWrapper
    from src.core.llm_client import LLMClientWrapper
    from src.core.execution_mode import BASELINE_MODE, VIKINGBOT_MODE, resolve_execution_mode
    from src.vikingbot_runner import prepare_openviking_config, stop_openviking_server
except SyntaxError as e:
    print(f"\n[Fatal Error] Syntax error while importing modules: {e}")
    sys.exit(1)
except ImportError as e:
    print(f"\n[Fatal Error] Cannot import modules: {e}")
    print(f"Current sys.path: {sys.path}\n")
    sys.exit(1)

# ==========================================
# 2. Helper Functions
# ==========================================

def load_config(config_path):
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"Config file not found: {config_path}")
    with open(config_path, 'r', encoding='utf-8') as f:
        raw = os.path.expandvars(f.read())
        return yaml.safe_load(raw)

def load_env_file(env_path):
    """
    Load simple KEY=VALUE entries from a .env file.

    Existing environment variables take precedence so CI/shell-provided values
    are not overwritten by local files.
    """
    if not env_path or not os.path.exists(env_path):
        return 0

    loaded = 0
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            text = line.strip()
            if not text or text.startswith("#"):
                continue
            if text.startswith("export "):
                text = text[len("export ") :].strip()
            if "=" not in text:
                continue
            key, value = text.split("=", 1)
            key = key.strip()
            value = value.strip()
            if not key:
                continue
            if (
                len(value) >= 2
                and value[0] == value[-1]
                and value[0] in {"'", '"'}
            ):
                value = value[1:-1]
            if key not in os.environ:
                os.environ[key] = value
                loaded += 1
    return loaded

def resolve_path(path_str, base_path):
    """
    Convert relative path to absolute path based on base_path.
    If path_str is already absolute, keep it unchanged.
    """
    if not path_str:
        return path_str
    if os.path.isabs(path_str):
        return path_str
    return os.path.normpath(os.path.join(base_path, path_str))

def is_placeholder_api_key(api_key):
    if not api_key:
        return True
    return str(api_key).strip() in {"your_api_key_here", "YOUR_API_KEY", "xxx", "sk-xxx"}

def load_vlm_api_key_from_ov_conf(ov_conf_path):
    if not ov_conf_path or not os.path.exists(ov_conf_path):
        return None
    try:
        with open(ov_conf_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(os.path.expandvars(f.read())) or {}
        api_key = data.get("vlm", {}).get("api_key")
        if isinstance(api_key, str) and api_key.startswith("${") and api_key.endswith("}"):
            api_key = os.environ.get(api_key[2:-1])
        return api_key if not is_placeholder_api_key(api_key) else None
    except Exception:
        return None

# ==========================================
# 3. Main Program
# ==========================================

def main():
    parser = ArgumentParser(description="Run Wiki Benchmark (Smart Path Handling)")
    default_config_path = os.path.join(SCRIPT_DIR, "config/config.yaml")

    parser.add_argument("--config", default=default_config_path,
                        help=f"Path to config file. Default: {default_config_path}")

    parser.add_argument("--step", choices=["all", "import", "build_wiki", "clear_wiki", "gen", "eval", "gen+eval", "del"], default="all",
                        help="Execution step: 'import', 'build_wiki', 'clear_wiki', 'gen', 'eval', 'gen+eval', 'del', or 'all'")

    parser.add_argument("--ov-conf", type=str, default=None,
                        help="Path to ov.conf file (default: benchmark/wiki/ov.conf)")

    parser.add_argument("--env-file", type=str, default=os.path.join(SCRIPT_DIR, ".env"),
                        help="Path to .env file with model settings (default: benchmark/wiki/.env)")

    args = parser.parse_args()

    # --- A0. Load .env before parsing configs with ${VAR} placeholders ---
    env_path = resolve_path(args.env_file, SCRIPT_DIR)
    loaded_env_count = load_env_file(env_path)
    if loaded_env_count:
        print(f"[Init] Loaded {loaded_env_count} variable(s) from: {env_path}")
    elif os.path.exists(env_path):
        print(f"[Init] Loaded .env from: {env_path} (all keys already existed in environment)")

    # --- A. Determine ov.conf path ---
    if args.ov_conf:
        ov_config_path = resolve_path(args.ov_conf, SCRIPT_DIR)
    else:
        ov_config_path = os.path.join(SCRIPT_DIR, "ov.conf")

    original_ov_config_path = ov_config_path
    if not os.path.exists(original_ov_config_path):
        print(f"[Warning] OpenViking config not found: {original_ov_config_path}")

    # --- B. Load and Parse Config ---
    config_path = os.path.abspath(args.config)
    print(f"[Init] Loading configuration from: {config_path}")

    try:
        config = load_config(config_path)
    except FileNotFoundError as e:
        print(f"[Error] {e}")
        return

    # --- C. Path Resolution ---
    print(f"[Init] Resolving paths relative to Project Root: {PROJECT_ROOT}")
    dataset_name = config.get('dataset_name', 'UnknownDataset')
    retrieval_topk = config.get('execution', {}).get('retrieval_topk', 5)

    format_vars = {
        'dataset_name': dataset_name,
        'retrieval_topk': retrieval_topk
    }

    path_keys = ['dataset_path', 'output_dir', 'vector_store', 'log_file', 'doc_output_dir']
    for key in path_keys:
        if key in config.get('paths', {}):
            original = config['paths'][key]
            rendered_path = original.format(**format_vars)
            resolved = resolve_path(rendered_path, PROJECT_ROOT)
            config['paths'][key] = resolved
            # print(f"  - {key}: {resolved}")

    if os.path.exists(original_ov_config_path):
        vlm_api_key = load_vlm_api_key_from_ov_conf(original_ov_config_path)
        if vlm_api_key and not os.environ.get("OPENAI_API_KEY"):
            os.environ["OPENAI_API_KEY"] = vlm_api_key
        config['_ov_conf_source_path'] = original_ov_config_path
        ov_config_path = prepare_openviking_config(config, original_ov_config_path)
        os.environ["OPENVIKING_CONFIG_FILE"] = ov_config_path
        from openviking_cli.utils.config.open_viking_config import OpenVikingConfigSingleton

        OpenVikingConfigSingleton.reset_instance()
        print(f"[Init] Using generated OpenViking config: {ov_config_path}")
    config['_ov_conf_path'] = ov_config_path

    # --- D. Initialize Components ---
    try:
        logger = setup_logging(config['paths']['log_file'])
        logger.info(">>> Benchmark Session Started")

        # 1. Adapter (Dynamic Loading)
        adapter_cfg = config.get('adapter', {})
        module_path = adapter_cfg.get('module', 'src.adapters.locomo_adapter')
        class_name = adapter_cfg.get('class_name', 'LocomoAdapter')

        logger.info(f"Dynamically loading Adapter: {class_name} from {module_path}")
        logger.info(f"Loading dataset from: {config['paths']['dataset_path']}")

        try:
            mod = importlib.import_module(module_path)
            AdapterClass = getattr(mod, class_name)
            adapter = AdapterClass(raw_file_path=config['paths']['dataset_path'])
        except ImportError as e:
            logger.error(f"Could not import module '{module_path}'. Please check your config 'adapter.module'. Error: {e}")
            raise e
        except AttributeError as e:
            logger.error(f"Class '{class_name}' not found in module '{module_path}'. Please check your config 'adapter.class_name'. Error: {e}")
            raise e

        # 2. Vector Store
        mode = resolve_execution_mode(config)
        skip_ingestion = bool(config.get('execution', {}).get('skip_ingestion', False))
        build_wiki_enabled = bool(config.get('execution', {}).get('build_wiki', False))
        will_import = args.step in ("all", "import") and not skip_ingestion
        if args.step == "import" and skip_ingestion:
            raise RuntimeError("execution.skip_ingestion=true conflicts with --step import")
        if args.step == "all" and skip_ingestion and not os.path.exists(config['paths']['vector_store']):
            raise RuntimeError(
                f"execution.skip_ingestion=true but vector store does not exist: {config['paths']['vector_store']}"
            )
        needs_vector_store = (
            mode == BASELINE_MODE
            or will_import
            or args.step == "build_wiki"
            or args.step == "clear_wiki"
            or (args.step == "all" and build_wiki_enabled)
            or args.step == "del"
        )
        if mode == VIKINGBOT_MODE and args.step in ("gen", "eval", "gen+eval"):
            needs_vector_store = False
        vector_store = (
            VikingStoreWrapper(store_path=config['paths']['vector_store'])
            if needs_vector_store
            else None
        )

        # 3. LLM Client
        api_key = os.environ.get(
            config['llm'].get('api_key_env_var', ''),
            config['llm'].get('api_key')
        )
        if is_placeholder_api_key(api_key):
            api_key = load_vlm_api_key_from_ov_conf(ov_config_path)
        if not api_key:
            logger.warning("No API Key found in config or environment variables!")

        llm_client = LLMClientWrapper(config=config['llm'], api_key=api_key)

        # 4. Pipeline
        pipeline = BenchmarkPipeline(
            config=config,
            adapter=adapter,
            vector_db=vector_store,
            llm=llm_client
        )

        # --- E. Execute Tasks ---
        if args.step in ["all", "import"]:
            if skip_ingestion:
                logger.info("Stage: Import skipped by execution.skip_ingestion=true")
            else:
                logger.info("Stage: Import (Data Prepare -> Ingest)")
                pipeline.run_import()

            if args.step == "all" and build_wiki_enabled:
                logger.info("Stage: Build Wiki")
                pipeline.run_build_wiki()

            if args.step == "all" and mode == BASELINE_MODE and not skip_ingestion:
                pipeline.db.close()
                pipeline.db = VikingStoreWrapper(store_path=config['paths']['vector_store'])

        if args.step == "build_wiki":
            logger.info("Stage: Build Wiki")
            pipeline.run_build_wiki()

        if args.step == "clear_wiki":
            logger.info("Stage: Clear Wiki")
            pipeline.run_clear_wiki()

        if args.step in ["all", "gen", "gen+eval"]:
            if mode == VIKINGBOT_MODE and pipeline.db is not None:
                pipeline.db.close()
                pipeline.db = None
            logger.info("Stage: Generation")
            pipeline.run_generation()

        if args.step in ["all", "eval", "gen+eval"]:
            logger.info("Stage: Evaluation (Judge -> Metrics)")
            pipeline.run_evaluation()

        if args.step in ["del"]:
            logger.info("Stage: Delete Vector Store")
            pipeline.run_deletion()

        logger.info("Benchmark finished successfully.")

    except KeyboardInterrupt:
        print("\n[Stop] Execution interrupted by user.")
    except Exception as e:
        if 'logger' in locals():
            logger.exception("Fatal error during execution")
        print(f"\n[Fatal Error] Program execution error: {str(e)}")
        sys.exit(1)
    finally:
        pipeline_obj = locals().get("pipeline")
        db = getattr(pipeline_obj, "db", None)
        if db is not None:
            try:
                db.close()
            except Exception:
                if 'logger' in locals():
                    logger.exception("Failed to close vector store")
                else:
                    print("[Warning] Failed to close vector store")
            finally:
                pipeline_obj.db = None
        stop_openviking_server()

if __name__ == "__main__":
    main()
