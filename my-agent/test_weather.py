import requests

# 测试地理编码
print("=== 测试地理编码 ===")
r = requests.get('https://geocoding-api.open-meteo.com/v1/search', params={'name': 'Beijing', 'count': 1}, timeout=10)
print(f"Status: {r.status_code}")
data = r.json()
results = data.get('results', [])
print(f"Results: {results}")

if results:
    lat = results[0]['latitude']
    lon = results[0]['longitude']
    name = results[0].get('name', '')
    print(f"\n城市: {name}, 纬度: {lat}, 经度: {lon}")

    # 测试天气查询
    print("\n=== 测试天气查询 ===")
    r2 = requests.get('https://api.open-meteo.com/v1/forecast', params={
        'latitude': lat,
        'longitude': lon,
        'current_weather': True,
        'timezone': 'auto',
    }, timeout=10)
    print(f"Status: {r2.status_code}")
    weather = r2.json()
    print(f"Response: {weather}")
