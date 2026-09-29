# Parsing bencoded data (integers, strings, lists, dictionaries)

def parse_int(data, i):
    # Integers start with 'i' and end with 'e' (i42e)
    i += 1  # skip 'i'
    j = data.index(b'e', i)
    val = int(data[i:j].decode())
    return val, j + 1

def parse_str(data, i):
    j = data.index(b':', i)
    length = int(data[i:j])
    j += 1
    s = data[j:j+length]
    return s, j+length

def parse_list(data, i):
    # Lists start with 'l' and end with 'e' (l4:spami42ee)
    i += 1  # skip 'l'
    arr = []
    while i < len(data) and data[i:i+1] != b'e':
        val, i = parse_any(data, i)
        arr.append(val)
    return arr, i + 1  # skip 'e'

def parse_dict(data, i):
    # Dictionaries start with 'd' and end with 'e' (d3:bar4:spame)
    i += 1  # skip 'd'
    d = {}
    while i < len(data) and data[i:i+1] != b'e':
        key, i = parse_str(data, i)
        val, i = parse_any(data, i)
        d[key] = val
    return d, i + 1  # skip 'e'

def parse_any(data, i):
    byte = data[i:i+1]
    if byte == b'i':
        return parse_int(data, i)
    elif byte == b'l':
        return parse_list(data, i)
    elif byte == b'd':
        return parse_dict(data, i)
    elif byte.isdigit():
        return parse_str(data, i)
    else:
        raise ValueError(f"Invalid bencode character at index {i}: {byte}")

def bdecode(data):
    if not isinstance(data, bytes):
        raise ValueError("Input must be a byte string")
    if not data:
        raise ValueError("Empty input")
    
    result, index = parse_any(data, 0)
    if index < len(data):
        raise ValueError(f"Extra data after parsing at index {index}")
    return result

def bencode(data):
    if isinstance(data, int):
        return b'i' + str(data).encode() + b'e'
    elif isinstance(data, bytes):
        return str(len(data)).encode() + b':' + data
    elif isinstance(data, str):
        # Convert string to bytes (assuming UTF-8)
        data = data.encode('utf-8')
        return str(len(data)).encode() + b':' + data
    elif isinstance(data, list):
        result = [b'l']
        for item in data:
            result.append(bencode(item))
        result.append(b'e')
        return b''.join(result)
    elif isinstance(data, dict):
        result = [b'd']
        # Sort keys to ensure consistent encoding 
        for key in sorted(data.keys()):
            val = data[key]
            encoded_key = key.encode('utf-8') if isinstance(key, str) else key
            result.append(bencode(encoded_key))
            result.append(bencode(val))
        result.append(b'e')
        return b''.join(result)
    else:
        raise ValueError(f"Unsupported type for bencoding: {type(data)}")
    
if __name__ == '__main__':
    print("")
    print("----BENCODE ENCODER & DECODER----- ")
    

    # Sample Python dictionary containing int, str, list, dict
    sample_data = {
        "name": "college_project.txt",
        "size": 1024,
        "tags": ["p2p", "torrent", "python"],
        "info": {
            "piece_length": 512,
            "private": 0
        }
    }

    print("\n1. Original Python Data Structure:")
    print("  ", sample_data)

    # Encode to bencode bytes
    bencoded_bytes = bencode(sample_data)
    print("\n2. Encoded to Bencode Bytes:")
    print("  ", bencoded_bytes)

    # Decode back from bencode bytes
    decoded_data = bdecode(bencoded_bytes)
    print("\n3. Decoded back to Python Objects:")
    print("  ", decoded_data)

    print("\nBencoding Successful!")