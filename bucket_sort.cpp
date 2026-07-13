#include <iostream>
#include <vector>
#include <algorithm>
#include <cmath>

using namespace std;

// 木桶排序函数
void bucketSort(vector<float>& arr) {
    if (arr.empty()) return;

    // 找到最大值和最小值
    float minVal = *min_element(arr.begin(), arr.end());
    float maxVal = *max_element(arr.begin(), arr.end());

    // 计算桶的数量（通常取数组长度）
    int bucketCount = arr.size();
    float range = (maxVal - minVal) / bucketCount;

    // 创建桶
    vector<vector<float>> buckets(bucketCount);

    // 将元素分配到各个桶中
    for (float num : arr) {
        int bucketIndex = min(bucketCount - 1,
                             static_cast<int>((num - minVal) / range));
        buckets[bucketIndex].push_back(num);
    }

    // 对每个桶内的元素进行排序
    for (auto& bucket : buckets) {
        sort(bucket.begin(), bucket.end());
    }

    // 将所有桶中的元素合并到原数组
    int index = 0;
    for (const auto& bucket : buckets) {
        for (float num : bucket) {
            arr[index++] = num;
        }
    }
}

// 整数版本的木桶排序
void bucketSortInt(vector<int>& arr) {
    if (arr.empty()) return;

    int minVal = *min_element(arr.begin(), arr.end());
    int maxVal = *max_element(arr.begin(), arr.end());

    int bucketCount = arr.size();
    int range = (maxVal - minVal) / bucketCount + 1;

    vector<vector<int>> buckets(bucketCount);

    for (int num : arr) {
        int bucketIndex = (num - minVal) / range;
        buckets[bucketIndex].push_back(num);
    }

    for (auto& bucket : buckets) {
        sort(bucket.begin(), bucket.end());
    }

    int index = 0;
    for (const auto& bucket : buckets) {
        for (int num : bucket) {
            arr[index++] = num;
        }
    }
}

// 打印数组
template<typename T>
void printArray(const vector<T>& arr) {
    for (const auto& num : arr) {
        cout << num << " ";
    }
    cout << endl;
}

int main() {
    // 测试浮点数排序
    vector<float> floatArr = {0.42, 0.32, 0.33, 0.52, 0.37, 0.47, 0.51};
    cout << "原始浮点数组: ";
    printArray(floatArr);

    bucketSort(floatArr);
    cout << "排序后浮点数组: ";
    printArray(floatArr);

    cout << endl;

    // 测试整数排序
    vector<int> intArr = {29, 25, 3, 49, 9, 37, 21, 43};
    cout << "原始整数数组: ";
    printArray(intArr);

    bucketSortInt(intArr);
    cout << "排序后整数数组: ";
    printArray(intArr);

    return 0;
}
