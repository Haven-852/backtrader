import re

path = r'E:\demo\backtrader\data_layer\collectors\advanced_collector.py'
with open(path, encoding='utf-8') as f:
    content = f.read()

# Fix redundant "self._pro or self._pro"
content = content.replace('pro = self._pro or self._pro', 'pro = self._pro')

# Fix status method
old_status = '''    def status(self) -> Dict:
        """返回采集器状态摘要"""
        return {
            "news": self._configured,
            "token_groups": {
                "news": bool(self.token_news),
                "report": bool(self.token_report),
                "flow": bool(self.token_flow),
                "fund": bool(self.token_fund),
                "index": bool(self.token_index),
            }
        }'''
new_status = '''    def status(self) -> Dict:
        """返回采集器状态摘要"""
        return {
            "configured": self._configured,
        }'''
content = content.replace(old_status, new_status)

with open(path, 'w', encoding='utf-8') as f:
    f.write(content)

print('Fixed')
