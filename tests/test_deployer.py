import unittest
from cr_infer.deployer import sanitize_service_name, CloudRunDeployer
from unittest.mock import MagicMock

class TestDeployer(unittest.TestCase):
    def test_sanitize_service_name(self):
        self.assertEqual(sanitize_service_name('vllm-Qwen/Qwen3.5-35B-A3B'), 'vllm-qwen-qwen3-5-35b-a3b')
        self.assertEqual(sanitize_service_name('Ollama:gemma:2b'), 'ollama-gemma-2b')
        self.assertEqual(sanitize_service_name('My.Custom.Service_123!'), 'my-custom-service-123')
        self.assertEqual(sanitize_service_name('123-model-name'), 'svc-123-model-name')
        self.assertEqual(len(sanitize_service_name('a' * 100)), 49)
        self.assertEqual(sanitize_service_name('vllm-meta-llama-meta-llama-3-1-405b-instruct-fp8---'), 'vllm-meta-llama-meta-llama-3-1-405b-instruct-fp8')

    def test_vpc_access_payload(self):
        mock_client = MagicMock()
        deployer = CloudRunDeployer(mock_client)
        payload = deployer.build_payload(
            name='test-service',
            region='europe-west4',
            image='vllm/vllm-openai',
            model_id='google/gemma-3-1b-it',
            bucket_name='west-4-models',
            gpu_type='nvidia-l4',
            framework='vllm',
            subnet='default',
            network='default'
        )
        self.assertIn('vpcAccess', payload['template'])
        self.assertEqual(payload['template']['vpcAccess']['egress'], 'ALL_TRAFFIC')
        self.assertEqual(payload['template']['vpcAccess']['networkInterfaces'], [{'subnetwork': 'default', 'network': 'default'}])

if __name__ == '__main__':
    unittest.main()
