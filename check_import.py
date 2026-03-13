try:
    print("Importing rlm.core.fast...")
    import rlm.core.fast
    print("Success fast!")
    
    print("Importing rlm.core.rlm...")
    import rlm.core.rlm
    print("Success rlm!")
    
    print("Importing rlm.core.lm_handler...")
    import rlm.core.lm_handler
    print("Success lm_handler!")

    print("Importing rlm.environments.local_repl...")
    import rlm.environments.local_repl
    print("Success local_repl!")
    
except Exception as e:
    import traceback
    traceback.print_exc()
