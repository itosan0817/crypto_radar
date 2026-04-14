import eth_abi
from web3 import AsyncWeb3
    
def decode_calldata(raw_calldata: str) -> str:
    """
    calldataをデコードし「何が起きるか」の構造体を文字列として返す。
    タイムロック変更予約(CallScheduled)のイベントログの場合、
    Dataフィールドにはパッキングされた引数が含まれるためパースを展開する。
    """
    if raw_calldata == "0x" or not raw_calldata:
        return "Calldata is empty"

    raw_bytes = bytes.fromhex(raw_calldata.replace("0x", ""))
    
    # タイムロックのCallScheduledイベントログからのデコード試行
    decoded = None
    try:
        # OZ Timelock v3/v4 (target, value, data, predecessor, delay)
        decoded = eth_abi.decode(['address', 'uint256', 'bytes', 'bytes32', 'uint256'], raw_bytes)
    except Exception:
        pass
        
    if not decoded:
        try:
            # OZ Timelock v5 (target, value, data, predecessor, salt, delay)
            decoded = eth_abi.decode(['address', 'uint256', 'bytes', 'bytes32', 'bytes32', 'uint256'], raw_bytes)
        except Exception:
            pass

    if decoded:
        target = decoded[0]
        value = decoded[1]
        payload = decoded[2].hex()
        delay = decoded[-1]
        
        payload_hex = "0x" + payload if payload else "0x"
        if not payload or payload == "0x":
            return f"Scheduled Target: {target}\nValue: {value}\nDelay: {delay} sec\nPayload: (empty)"
            
        method_id = payload_hex[:10]
        args_length = (len(payload_hex) - 10) // 2
        return f"Scheduled Target: {target}\nValue: {value}\nDelay: {delay} sec\nPayload Method ID: {method_id}\nPayload Args Length: {args_length} bytes\nRaw Payload: {payload_hex}"

    # デコード失敗時（単純なCalldataとしてパースをフォールバック）
    try:
        method_id = raw_calldata[:10]
        args_length = (len(raw_calldata) - 10) // 2
        return f"Method ID: {method_id}, Args Length: {args_length} bytes (Raw parse)"
    except Exception as e:
        return f"Failed to decode calldata: {e}"

def track_proxy_implementation(w3: AsyncWeb3, proxy_addr: str) -> str:
    """
    対象アドレスがProxyであった場合、EIP-1967等に従ってImplementationアドレスを特定する。
    """
    return proxy_addr # プレースホルダー実装
