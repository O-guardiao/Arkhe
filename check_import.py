try:
    print("Importing rlm.core.optimized.fast...")
    import rlm.core.optimized.fast
    print("Success fast!")
    
    print("Importing rlm.core.engine.rlm...")
    import rlm.core.engine.rlm
    print("Success rlm!")
    
    print("Importing rlm.core.engine.lm_handler...")
    import rlm.core.engine.lm_handler
    print("Success lm_handler!")

    print("Importing rlm.environments.local_repl...")
    import rlm.environments.local_repl
    print("Success local_repl!")
    
except Exception as e:
    import traceback
    traceback.print_exc()
